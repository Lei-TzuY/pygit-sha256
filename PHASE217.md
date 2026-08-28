# Phase217 — Promisor-aware reset batching

Phase217 extends partial-clone bulk materialization from checkout to reset operations that must construct repository-visible SHA-256 blob identities.

## Behavior

A filtered partial clone may retain the target commit/tree graph while leaving blobs promised. Phase217 wraps `Repository.reset()` before the historical implementation mutates refs, the index, or the worktree:

1. `--soft` delegates immediately because it moves refs only;
2. `--mixed` and `--hard` check for unresolved promises;
3. the target revision is resolved before reset mutation begins;
4. the already-local commit/tree graph is walked and unresolved promised blob native OIDs are collected;
5. the deduplicated set is materialized through the existing promisor batch transport;
6. the original reset implementation remains authoritative for refs, reflogs, index rebuilding, worktree replacement, and operation-state cleanup.

For `--hard`, the materialized blobs are needed to restore the target worktree. For `--mixed`, pygit must rebuild its index with local SHA-256 blob IDs even though the worktree is left untouched.

If revision resolution or promisor materialization fails, reset has not yet moved HEAD or rewritten the index/worktree.

## Git compatibility and the SHA-256-native boundary

Git documents `reset --mixed` as resetting the index but not the working tree, while `reset --hard` resets both index and working tree. Git's partial-clone design also warns that fetching missing objects one at a time is slow and uses bulk prefetch for operations that know a set of objects will be needed.

There is an important pygit-specific identity constraint. A native Git partial clone can keep native object IDs in its index because transport and repository object format agree. pygit intentionally does not: foreign smart-HTTP object IDs are SHA-1, while every persistent pygit index entry is a local SHA-256 object ID. A foreign tree entry that is still promised exposes only its original native SHA-1 identity; deriving its real local SHA-256 requires the blob content. Therefore `reset --mixed` must materialize promised target blobs before rebuilding the SHA-256-native index. Storing native SHA-1 in the index or inventing surrogate SHA-256 IDs would violate the repository design.

`reset --soft` needs neither index nor worktree blob identities and remains completely network-free.

## SHA-256-native identity

No object format changes are introduced. Native SHA-1 remains confined to the promisor/protocol lookup boundary. Materialized blobs are imported under their real content-derived SHA-256 IDs, while existing foreign commit/tree SHA-256 identities remain stable and are never rewritten.

## Compatibility seams

- one missing object still uses the Phase213 single-object fetch path;
- multiple missing objects use one Phase214 batch request;
- ordinary repositories remain network-free;
- soft reset remains network-free;
- mixed reset materializes only because the persistent index requires local SHA-256 identities;
- the original `Repository.reset` implementation remains responsible for all reset mutation semantics.
