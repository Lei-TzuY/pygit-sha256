# Phase 231 — promisor-aware `ls-tree` batching

Phase 231 removes one-object-at-a-time lazy fetching from `ls-tree` when a partial clone contains unresolved promised blobs.

## Behavior

`pygit ls-tree` must expose repository-visible SHA-256 object identities. A foreign native-reference tree can locally preserve the source Git SHA-1 for an omitted blob, but that SHA-1 cannot be emitted as a pygit object name and must not be converted into a surrogate SHA-256. When a selected blob is still promised, pygit therefore materializes the real blob content and derives its real local SHA-256 before producing the structured `LsTreeEntry`.

Before Phase 231, the normal traversal accessed `TreeEntry.sha` one entry at a time. On a filtered clone this could turn a single `ls-tree` invocation into N promisor fetches. Phase 231 adds a metadata-only planning pass that mirrors the existing recursion and pathspec rules while deliberately avoiding `TreeEntry.sha` for blobs. The selected unresolved native OIDs are deduplicated and passed to the established multi-promisor materializer once.

The planner preserves the existing boundaries:

- ordinary repositories do not enter promisor fetching;
- one selected missing blob keeps the Phase 213 single-object seam;
- multiple selected blobs use one bulk request;
- pathspecs narrow the object demand, so unmatched promises stay unresolved;
- per-remote `serverOption`, multi-promisor fallback, primary-promisor-last ordering, and shrinking remaining batches are inherited from the existing materializer;
- native SHA-1 remains confined to foreign-tree/promisor/protocol interoperability;
- returned `LsTreeEntry.oid` values remain real content-derived SHA-256 object IDs;
- no tree serialization, index, ref, pack, or protocol format changes are introduced.

## Git compatibility

Git partial-clone design explicitly warns that one-at-a-time dynamic object fetching is expensive and recommends bulk prefetch when a command can predict the missing object set. `ls-tree` is deterministic once its tree-ish, recursion flags, and pathspec are known, so it is a natural place to collapse predictable lazy fetches.

This phase intentionally keeps pygit's existing SHA-256-visible `ls-tree` contract. A future metadata-only representation could allow modes such as `--name-only` to avoid materializing blobs entirely, but that requires separating path reporting from object-identity construction rather than leaking native SHA-1 into the public result.

## Verification focus

`tests/test_phase231.py` covers:

- one bulk request for two selected promised blobs;
- exact `serverOption` forwarding;
- pathspec narrowing that leaves an unrelated promise unresolved;
- preservation of the Phase 213 single-object fetch seam;
- real 64-hex SHA-256 identities in returned entries;
- ordinary repository transparency with no promisor prefetch.
