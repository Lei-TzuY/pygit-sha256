# Phase 129 — cherry-pick and rebase conflict index stages

Phase 129 extends the multi-stage conflict model from ordinary merges to replay workflows. Cherry-pick and rebase now publish the exact objects participating in an unresolved replay into index stages 1, 2, and 3.

## Replay stage semantics

For a commit being replayed onto the current HEAD:

```text
stage 1    source commit's first parent (base)
stage 2    current HEAD receiving the replay (ours)
stage 3    source commit itself (theirs)
```

This is the same three-way model used by Git. Missing sides remain absent rather than being represented by synthetic empty blobs, so add/add and modify/delete conflicts have the expected asymmetric stage sets.

## Cherry-pick

A conflicting `pygit cherry-pick` now immediately exposes its sides through the existing plumbing:

```bash
pygit ls-files --stage
pygit cat-file -p ':1:conflict.txt'
pygit checkout-index --stage=2 --prefix=ours/ conflict.txt
pygit checkout-index --stage=3 --prefix=theirs/ conflict.txt
```

Normal `pygit add` or `pygit rm` resolution collapses the path back to stage 0. `cherry-pick --continue` commits the resolved stage-0 tree; `cherry-pick --abort` restores the pre-pick HEAD and clears unmerged records.

## Rebase

Rebase already reuses the cherry-pick three-way engine internally. Phase 129 hooks that shared replay point, so a rebase conflict publishes:

- stage 1 from the original replayed commit's parent;
- stage 2 from the current rebased HEAD / onto history;
- stage 3 from the original replayed commit.

`rebase --abort` restores the original branch tip and removes unmerged stages. `rebase --skip` clears the skipped commit's stages **before** replaying any later commits; this is important because Phase 127 intentionally rejects write-tree operations while unmerged stages remain.

## Architecture

`pygit.replay_index_stages` installs an idempotent bridge onto the existing `Repository` class:

1. `_apply_cherry_pick()` is wrapped once; both direct cherry-pick and rebase flow through this method.
2. On conflict, `populate_replay_conflict_stages()` derives the source parent, current HEAD, and source tree and publishes stages 1/2/3.
3. Cherry-pick/rebase state cleanup removes any operation-owned unmerged stages.
4. `rebase_skip()` clears unmerged records before the legacy continuation loop starts the next replay.

The three-way merge algorithm, conflict-marker formatting, rerere behavior, and operation state-file formats are unchanged.

## Compatibility

Stage 0 remains the only commit-ready state. Existing low-level manually constructed stage records are not changed merely by importing the package or making ordinary commits. Cleanup is tied to real cherry-pick/rebase operation state.

## Regression coverage

`tests/test_phase129.py` covers exact cherry-pick stage identities and plumbing visibility, add/continue resolution, cherry-pick abort, exact rebase stage identities, rebase skip ordering, rebase abort restoration, and asymmetric modify/delete replay conflicts.
