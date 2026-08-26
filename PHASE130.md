# Phase 130 — three-way `read-tree` index merges

Phase 130 connects Phase 124's persistent multi-stage index to Git's low-level three-tree merge workflow. Conflict stages no longer need to be created only through hand-written `update-index --index-info` records: `read-tree -m BASE OURS THEIRS` can now populate stages 1, 2, and 3 directly from tree objects.

## Three-tree merge

```bash
pygit read-tree -m BASE OURS THEIRS
pygit ls-files -u
```

The merge is index-only. It resolves all three tree-ish arguments through the existing tree resolver, recursively flattens their trees, computes the complete result before publication, then replaces the current index.

For each ordinary non-directory/file-conflict path, Phase 130 follows Git's trivial three-tree rules:

- if ours and theirs are identical and present, write that value at stage 0;
- if ours equals base and theirs is present, take theirs at stage 0;
- if theirs equals base and ours is present, take ours at stage 0;
- otherwise preserve each available side as stage 1 (base), stage 2 (ours), and stage 3 (theirs).

Object identity includes the tree mode as well as the SHA-256 object ID, so executable-bit, symlink, and gitlink mode changes participate in the same merge decisions.

## Deletions and `--aggressive`

Native `git read-tree -m` deliberately leaves several deletion-only trivialities unmerged by default. Phase 130 preserves that distinction. For example, if both sides delete a base path, default mode retains only stage 1; if ours equals base and theirs deletes, default mode retains stages 1 and 2.

`--aggressive` enables the additional trivial deletion resolutions:

```bash
pygit read-tree -m --aggressive BASE OURS THEIRS
```

With it, both-side deletion and one-side deletion where the other side equals base collapse cleanly instead of leaving conflict stages.

## `ls-files --unmerged`

The stage-aware `ls_files(..., unmerged=True)` helper already existed after Phase 124. Phase 130 exposes it through the installed command:

```bash
pygit ls-files -u
pygit ls-files --unmerged -z
```

Only stages 1–3 are emitted, always with mode, object ID, stage number, and path. Existing `--stage`, `--cached`, path filtering, `--error-unmatch`, and NUL framing remain unchanged.

## Replacement semantics after Phase 124

A normal replacing `read-tree TREE` or `read-tree --empty` now discards old conflict stages together with the previous stage-0 index. This closes a post-Phase-124 compatibility gap where the historical reader replaced only `Index.entries`, allowing stale stages 1–3 to survive a replacement command.

`read-tree --prefix` remains additive and therefore preserves existing conflict stages.

## Scope boundary

Phase 130 intentionally implements the focused three-tree, index-only merge path. It does not pretend to support:

- two-tree `read-tree -m` carry-forward semantics;
- `read-tree -m -u` worktree materialization;
- directory/file conflict expansion;
- higher-level textual/content merging.

Directory/file conflicts fail before index publication. The worktree is never modified by the Phase 130 three-tree merge path.

## Regression coverage

`tests/test_phase130.py` covers:

- persistent stage 1/2/3 creation for a true three-way conflict;
- all core clean trivial-resolution cases;
- native default deletion-stage behavior and `--aggressive` deletion resolution;
- index-only operation with untouched worktree bytes;
- atomic rejection of directory/file conflicts;
- installed `read-tree -m` plus `ls-files -u` and NUL framing;
- replacement and `--empty` removal of stale conflict stages;
- invalid merge argument combinations leaving the prior index intact.
