# Phase418: force-move branches selected by previous checkout history

Phase418 extends Phase415 with Git-compatible forced branch rename forms:

```text
pygit branch -M @{-N} <new>
pygit branch --move --force @{-N} <new>
pygit branch --force --move @{-N} <new>
```

The source selector must still expand to a real local branch. The destination may already exist only in forced mode.

## Forced destination replacement

When the destination ref exists and is not the currently checked-out branch, Phase418 replaces its loose/packed identity with the source branch tip. The destination's previous reflog history is discarded; the source reflog history becomes the destination reflog and receives the same native rename subject used by Phase415:

```text
Branch: renamed refs/heads/<old> to refs/heads/<new>
```

If the selected source branch is currently checked out and the destination is a different non-current branch, symbolic HEAD follows the rename. If the destination is the currently checked-out branch, forced replacement is rejected before mutation, matching native Git worktree safety.

A non-current source==destination force move is accepted and appends the same-OID rename reflog event, matching Git. A current destination remains protected even for that shape.

Phase415's config/ref/HEAD/reflog/packed-refs snapshot rollback remains the mutation boundary, so failed forced replacement restores both source and overwritten destination state.

## Native differential

The CI differential initializes a native SHA-256 Git repository, creates `topic` and `taken`, advances `topic`, checks out `main`, then runs:

```text
git branch -M @{-1} taken
```

It verifies silent success, `taken` now pointing at the former `topic` tip, HEAD remaining on `main`, and the exact native rename reflog subject.

## Scope and invariants

This phase changes only branch/ref/config/reflog porcelain. No object hashing, serialization, packfiles, transport, promisor state, object maps, shallow state, FETCH_HEAD, or storage format changes. All local branch OIDs remain genuine content-derived 64-hex SHA-256 values.

## Coordination

- exact base: Phase415 / PR #378 head `195036b9389277dd97b755efa596a430ac3681f0`;
- Phase415 authoritative Tests #3342 / run `33551295086`: success;
- Python 3.9 / 3.13 on that base: 1385 passed each;
- CI Git on that base: 2.55.0;
- Phase416 and Phase417 are parallel rev-parse / branch-copy lines and are intentionally not duplicated;
- Phase418 namespace was collision-checked immediately before creation.

This phase intentionally remains a stacked, open, unmerged pull request.
