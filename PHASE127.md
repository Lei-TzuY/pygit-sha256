# Phase 127 — porcelain merge conflict index stages

Phase 127 connects high-level `pygit merge` conflicts to the persistent Git-style multi-stage index introduced in Phase 124 and the stage extraction plumbing added in Phase 126.

Before this phase, a textual merge conflict wrote conflict markers to the working tree and recorded the pathname in `.pygit/MERGE_CONFLICTS`, but the index retained its old stage-0 entry. The low-level stage model therefore existed without being populated by a real porcelain merge.

## Real merge stages

When `Repository.merge()` stops on an unresolved conflict, every conflicted path now drops its stage-0 record and receives the object records that actually participated in the merge:

```text
stage 1    merge-base version
stage 2    ours / pre-merge HEAD version
stage 3    theirs / MERGE_HEAD version
```

This makes the normal conflict workflow immediately visible through existing plumbing:

```bash
pygit ls-files --stage
pygit rev-parse ':1:conflict.txt'
pygit cat-file -p ':2:conflict.txt'
pygit checkout-index --stage=3 --prefix=theirs/ conflict.txt
```

The conflict-marker file in the working tree remains unchanged. The stage records are metadata pointing at the original stored objects, so inspecting a side does not rewrite history or manufacture replacement blobs.

## Asymmetric conflicts

Stages are only created for sides that exist. This follows Git's unmerged-index model rather than forcing synthetic empty blobs:

- modify/delete may have stages 1 and 2 but no stage 3;
- delete/modify may have stages 1 and 3 but no stage 2;
- add/add may have stages 2 and 3 but no stage 1.

Path expressions for a missing stage continue to fail normally instead of silently falling back to another side.

## Resolution

Normal worktree staging keeps the Phase 124 behavior:

```bash
# edit conflict.txt
pygit add conflict.txt
```

`add` removes stages 1-3 for the path, installs one resolved stage-0 entry, and clears the pathname from `MERGE_CONFLICTS`. Once every conflict is resolved, the ordinary merge commit path records both parents.

`rm` likewise removes every stage for a conflicted path and marks that path resolved.

## Abort and cleanup

`pygit merge --abort` restores the original HEAD worktree/index and now also removes the unmerged stage records created for the aborted merge. Successful merge completion similarly leaves no stale stages behind.

The cleanup hook is deliberately conditional on an actual `MERGE_HEAD`; ordinary commits do not erase manually constructed low-level conflict stages.

## Architecture

The large historical `repo.py` porcelain module is left unchanged. `pygit.merge_index_stages` installs a small, idempotent bridge on the existing `Repository` class at package initialization:

1. `_write_merge_state()` is wrapped to derive the merge base, ours, and theirs commit trees and publish stages 1-3 before writing merge operation metadata.
2. `_clear_merge_state()` is wrapped to clear unmerged records only when a real merge operation was active.

Because Python executes `pygit/__init__.py` before package submodules, internal `from .repo import Repository` consumers and public `from pygit import Repository` consumers observe the same patched class object.

## Scope boundary

Phase 127 migrates **merge** conflicts. Cherry-pick and rebase still reuse `_apply_three_way()` but maintain their own operation-state files; migrating those workflows to the same stage model is the natural next phase.

## Regression coverage

`tests/test_phase127.py` covers:

- real content conflicts producing exact stage 1/2/3 object IDs;
- shared revision and `ls-files --stage` visibility;
- Phase 126 `checkout-index --stage=N` extraction from a real merge;
- installed `python -m pygit` command routing;
- `git add`-style conflict resolution followed by a two-parent merge commit;
- merge abort restoring stage 0 and clearing unmerged records;
- modify/delete conflicts omitting the nonexistent stage 3.
