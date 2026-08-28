# Phase 223 — Promisor-aware `commit --only` batching

Phase223 removes a partial-clone network waterfall from path-limited commits while preserving pygit's SHA-256-native index and tree model.

## Problem

`Repository.commit(..., only_paths=[...])` implements Git-style path-limited commit behavior by temporarily rebuilding the index from `HEAD`, staging only the selected working-tree paths into that snapshot, committing it, and then preserving unrelated staged work.

For an ordinary repository this is entirely local.  For a filtered partial clone, however, retained foreign trees may contain unresolved blob entries represented by native Git SHA-1 object ids.  Rebuilding pygit's persistent temporary index requires real local SHA-256 object ids for every entry in the HEAD snapshot.  The historical tree flattening therefore touched each promised blob independently and could trigger one demand-fetch request per object.

## Change

Before the historical `only_paths` implementation traverses HEAD, Phase223:

1. detects an active promisor state and a path-limited commit;
2. collects the complete deduplicated set of unresolved promised blobs reachable from the current HEAD tree;
3. materializes that set through the existing multi-promisor demand-fetch layer in one bulk request where more than one object is missing;
4. lets the existing commit implementation rebuild its temporary SHA-256-native index and preserve all established commit semantics.

Single-object materialization keeps the existing Phase213 single-object compatibility seam.  Multi-promisor ordering, per-remote `serverOption`, primary-promisor-last behavior, and batch shrinking remain delegated to the existing promisor materializer.

## Why all HEAD blobs are materialized

This phase intentionally does not claim path-only object materialization.  pygit's persistent index and ordinary tree objects store local SHA-256 object ids.  An unresolved foreign tree entry has only its native Git SHA-1 identity until its blob content is available.  A path-limited commit reconstructs the complete HEAD snapshot in a temporary index, so every retained HEAD blob needs a real content-derived local SHA-256 id.

Using a native SHA-1 as an index id, inventing a surrogate SHA-256 id, or silently dropping unchanged paths would violate repository identity or commit correctness.  A future mixed native/local tree synthesis layer could narrow the materialized object set without those compromises.  Phase223 takes the safe immediate improvement: N independent network round trips become one deduplicated bulk fetch.

## Compatibility

- commits without `only_paths` do not gain a new prefetch;
- ordinary repositories remain on the historical network-free path;
- already-resolved partial snapshots perform no additional fetch;
- selected-path staging, unrelated staged-entry preservation, commit hooks, parent selection, amend/fixup/squash behavior, and commit object construction remain owned by the existing implementation;
- no protocol format, pack format, tree serialization, or object identity changes are introduced.

## SHA-256-native boundary

Promisor requests continue to use native SHA-1 only at the Git interoperability boundary.  Materialized blob contents are written to pygit's object store under their real content-derived SHA-256 ids; the temporary index and resulting ordinary pygit trees contain those SHA-256 ids.  No surrogate object ids are created.

## Tests

`tests/test_phase223.py` covers:

- one bulk request for multiple unresolved HEAD blobs during `commit(..., only_paths=...)`;
- preservation of unchanged paths in the resulting commit tree;
- per-remote `serverOption` forwarding through the existing materializer;
- no Phase223 prefetch for commits without `only_paths`;
- no promisor/network activity for ordinary repositories.
