# Phase217 — Promisor-aware `reset --hard` batching

Phase217 extends the partial-clone worktree materialization model from checkout to hard reset.

## Behavior

`Repository.reset(..., mode="hard")` restores the target commit's tracked files. In a filtered partial clone, some target blobs may still be promised and absent locally. Rather than allowing tree-entry resolution to fault those blobs in one at a time, Phase217:

1. checks whether unresolved promisor objects exist;
2. resolves the hard-reset target before any ref/index/worktree mutation;
3. walks the already-local commit/tree graph and collects unresolved promised blob native OIDs;
4. materializes the deduplicated set through the existing batch promisor transport;
5. delegates the actual reset to the historical implementation.

The materialization happens before HEAD moves. If the promisor remote is unavailable, the reset fails without partially moving the branch or rewriting the worktree.

`--soft` and `--mixed` do not restore blob contents, so they bypass promisor materialization completely.

## Git compatibility

Git documents `reset --hard` as updating the working tree and index to match the target commit. Git's partial-clone design also calls out bulk prefetch for worktree-updating operations because fetching missing blobs one at a time is slow. Phase217 follows the same principle while leaving pygit's existing reset semantics authoritative.

## SHA-256-native identity

No object format changes are introduced. Promised objects continue to be addressed by native SHA-1 only at the promisor/protocol boundary. Materialized blobs are imported under their real local SHA-256 object ids, and existing commit/tree SHA-256 identities are not rewritten.

## Compatibility seams

- one missing object still uses the Phase213 single-object fetch path;
- multiple missing objects use one Phase214 batch request;
- ordinary repositories remain network-free;
- non-hard reset modes remain network-free;
- the original `Repository.reset` implementation remains responsible for ref updates, index rebuilding, operation-state cleanup, reflogs, and worktree replacement.
