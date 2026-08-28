# Phase218 — Promisor-aware commit worktree transitions

Phase218 moves partial-clone batching one level below individual commands by wrapping the shared `Repository._replace_worktree_from_commit()` primitive.

## Why this layer

Phase216 batches promises for full `checkout`, and Phase217 batches reset operations that rebuild pygit's SHA-256 index. Several other commands restore an entire commit through the same private worktree primitive, including:

- fast-forward merge;
- merge abort;
- rebase fast-forward, setup, abort and skip transitions;
- bisect checkout and bisect reset;
- hard reset after Phase217 has completed its own prefetch;
- historical clone callers that use the primitive.

Without a common guard, a partial clone that reaches any of those paths can encounter unresolved foreign tree entries one at a time and degrade into one promisor request per blob.

## Behavior

Before `_replace_worktree_from_commit()` mutates the worktree or index, Phase218:

1. checks whether unresolved promises exist at all;
2. walks only the target commit/tree snapshot using the already-local commit/tree graph;
3. collects unresolved promised blob native SHA-1 ids;
4. deduplicates and materializes that complete set through the existing Phase214 materializer;
5. delegates the actual transition to the historical implementation unchanged.

If the repository has promises belonging only to other snapshots and the target commit needs none of them, no network request is made.

Materialization failure occurs before the historical primitive removes files, rebuilds the index, or writes target contents. High-level callers such as fast-forward merge therefore also retain their pre-transition refs when the promisor remote cannot supply required objects.

## Compatibility

- ordinary repositories remain network-free;
- already-materialized partial snapshots remain network-free;
- one missing blob still uses the Phase213 `_fetch_native_object` seam;
- multiple missing blobs use one Phase214 `_fetch_native_objects` request;
- Phase216 checkout and Phase217 reset wrappers remain installed independently;
- when Phase217 hard reset prefetches the target first, the Phase218 primitive sees no remaining target promises and becomes a no-op;
- the original `_replace_worktree_from_commit` remains authoritative for index and worktree mutation semantics.

## SHA-256-native identity

No object format or identity rule changes. Native SHA-1 remains only the interoperability/promisor lookup key. Materialized blobs are stored under their real content-derived local SHA-256 ids, and the existing foreign tree/commit SHA-256 identities remain stable.

## Scope

Path-limited restore is intentionally not folded into this phase. `checkout_paths()` currently flattens the target tree as part of its historical implementation; a correct partial-clone optimization should be pathspec-aware instead of eagerly materializing the whole snapshot. That is a separate follow-up.
