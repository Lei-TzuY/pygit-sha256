# Phase225: Promisor-aware diff batching

Phase225 removes another partial-clone N-request waterfall by teaching commit-backed diff modes to prefetch the complete set of missing blobs they already know they will need.

## Behavior

`Repository.diff()` now batches unresolved promised blobs before historical tree flattening when the diff is backed by one or more commits:

- `diff(from_ref=A, to_ref=B)` batches the deduplicated union of promises reachable from A and B.
- `diff(from_ref=A)` batches promises reachable from A before comparing that snapshot with the working tree.
- `diff(cached=True)` batches promises reachable from HEAD before comparing HEAD with the SHA-256-native index.
- a plain working-tree-vs-index diff remains on the original path and does not prefetch unrelated historical promises.

The existing renderer remains the owner of all diff formatting and comparison semantics. Phase225 only predicts and materializes objects before that renderer consumes unresolved foreign `TreeEntry.sha` values.

## Why complete snapshots

The current commit diff path first flattens each selected commit tree into local `(path, SHA-256, mode)` entries. A foreign promised tree entry has only its native Git SHA-1 until the blob contents arrive. As with the earlier merge/reset/commit batching phases, pygit cannot place that native SHA-1 into a local SHA-256 tree representation and must not invent a surrogate identifier.

Phase225 therefore batches the complete unresolved blob set for every commit snapshot that the historical diff path will flatten. This removes per-entry network round trips without changing object identity. A future mixed native/local tree comparison layer could narrow this set further by comparing unchanged native identities before materialization.

## Git compatibility

Git's partial-clone design explicitly notes that dynamic one-object-at-a-time fetching is expensive and describes bulk prefetch as the intended optimization when an operation can predict a required object set. It even calls out `git log -p` as a candidate for a prefetch pass using object traversal. Phase225 applies that principle to pygit's existing commit diff modes.

## SHA-256-native boundary

Native SHA-1 remains confined to foreign tree/promisor metadata and protocol requests. Fetched blobs are imported under their content-derived local SHA-256 identities, and the existing diff/index/tree code continues to consume only real SHA-256 object IDs.

No tree serialization, pack protocol, ref, or index format changes are introduced.

## Follow-up

The next logical step is promisor-aware history patch generation (`log -p`, `-L`, and follow/rename traversal), where historical snapshots can still encounter the same per-object demand-fetch pattern over many commits.
