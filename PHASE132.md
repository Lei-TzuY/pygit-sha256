# Phase 132 — two-tree `read-tree` carry-forward merges

Phase 132 fills the fast-forward merge mode left open by Phase 130. `pygit read-tree -m H M` now advances an index derived from old tree `H` toward destination tree `M` while preserving compatible staged changes instead of blindly replacing the index.

## Command

```bash
pygit read-tree -m HEAD NEW_HEAD
```

The operation is intentionally **index-only**. It does not update worktree bytes; `read-tree -m -u` remains unsupported.

## Carry-forward rules

For each path, Phase 132 compares the current stage-0 index value `I`, the old tree value `H`, and the destination tree value `M`, including both SHA-256 object identity and mode.

- `I == H`: there is no staged change relative to the old tree, so adopt `M` (including additions/deletions).
- `H == M`: the trees did not change the path, so preserve the staged index state.
- `I == M`: the staged state already matches the destination, so preserve it.
- a path present only in the index is carried forward as a local staged addition.
- an empty index with `H == M` uses `M`, matching Git's initial-checkout exception instead of interpreting the missing index path as a staged deletion.
- any remaining case represents simultaneous incompatible index and tree changes and fails without publishing a partial result.

These are the focused index-side carry-forward semantics behind Git's two-tree fast-forward `read-tree -m` mode. Full native worktree cleanliness checks are intentionally deferred until `-m -u` support exists.

## Safety

Two-tree merge refuses to start when the index already contains stages 1-3. All tree-ish values are resolved and all result entries are constructed before the index mappings are replaced, so resolution failures, object errors, directory/file conflicts, and staged-change conflicts leave the old index intact.

The worktree is never modified by this phase.

## CLI compatibility

`read-tree -m` now accepts exactly two or three tree-ish arguments:

- two trees: Phase 132 carry-forward fast-forward mode;
- three trees: Phase 130 stage-aware trivial three-way merge.

`--aggressive` remains three-tree-only. One-tree `-m`, two-tree `-m -u`, and broader native worktree/index freshness checks remain future work.

## Regression coverage

`tests/test_phase132.py` covers clean update/add/delete fast-forwards, preservation of local staged changes when trees agree, staged deletion and local-add carry-forward, indexes already matching the destination, atomic conflict rejection, the empty-index initial-checkout rule, rejection of pre-existing unmerged stages, installed CLI routing, and untouched worktree bytes.
