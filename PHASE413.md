# Phase413 — `branch <name> @{-N}` previous-checkout start points

Phase413 extends the exact-green previous-checkout stack with Git-compatible branch creation from checkout history.

## Behavior

`pygit branch <new> @{-N}` expands the selector through the existing HEAD-reflog-backed resolver, resolves the resulting branch name or detached commit OID to a genuine local commit, and writes only the new branch ref.

The focused adapter intentionally does not call legacy `Repository.branch(..., start_point=...)`: that historical API also attaches HEAD to the newly-created branch. Native `git branch` must leave HEAD and the worktree untouched, so Phase413 preserves the current branch exactly and emits no success output, matching native Git.

Missing history fails before the new ref is created. A literal `-` is deliberately *not* normalized to `@{-1}` here. Native Git accepts that shorthand for checkout, but `git branch <new> -` rejects `-` as an invalid object name.

## Native Git differential

A focused SHA-256 regression performs `main -> topic -> main`, runs `git branch new @{-1}`, and verifies that `new` equals `topic`, HEAD remains on `main`, and successful branch creation is silent. The same semantic assertions are applied to pygit.

## SHA-256-native invariant

Previous detached destinations remain genuine local content-derived 64-hex SHA-256 commit IDs. Selector text is never padded, truncated, rehashed, translated into a surrogate identity, or persisted as an object ID. This phase does not change object serialization, packfiles, protocol behavior, FETCH_HEAD, native mappings, or storage format.

## Coordination and CI history

Phase413 is based exactly on Phase412 / PR #373 head `e8a518932d5fe9cf2361e9ce86a82379dd79a44b`, whose Tests #3298 / run `33532897688` completed successfully. The `phase413` namespace was collision-checked before branch creation.

The first Phase413 authoritative run, Tests #3308 / run `33533394847`, completed with 1368 passed and 3 failed. All three failures exposed the same compatibility defect: the focused adapter delegated to legacy `Repository.branch`, which attached HEAD to the new branch. The corrective implementation now writes only the branch ref and keeps HEAD unchanged; tests also assert native Git's silent stdout behavior.
