# Phase278: trusted promisor sizes for `blob:limit`

Phase278 connects Phase276's trusted promisor size sidecar to the existing `rev-list --filter=blob:limit=<n>[kmg]` implementations.

## Behavior

An unresolved promised blob can now be classified without materializing its contents when `.pygit/promisor.json` contains a trusted uncompressed size for its native object ID.

The existing Git-compatible membership rule remains unchanged:

- blobs smaller than the threshold are kept;
- blobs whose size is equal to or greater than the threshold are filtered;
- `k`, `m`, and `g` use binary units.

The same metadata-only decision is used by both the ordinary line/count adapter and the ordered `--in-commit-order` adapter.

## Missing-object presentation

A promised blob that is smaller than the threshold remains in the structured inventory. Existing `--missing=allow-promisor`, `--missing=print`, and `--missing=print-info` presentation policy therefore remains authoritative.

A promised blob at or above the threshold is removed by the filter before missing-object presentation. This prevents a filtered blob from leaking through a `?native-oid` diagnostic merely because its content is absent locally.

Missing non-blob objects are not affected by `blob:limit`.

## Strict fallback

Trusted size metadata is optional enrichment, not guessed state. If an unresolved promised blob has no stored size, `blob:limit` still fails before rendering output.

The filter never falls back to `_fetch_native_object()` or `_fetch_native_objects()` merely to learn the size. This preserves partial-clone behavior and prevents a metadata-only query from becoming an implicit content download.

## SHA-256-native boundary

Promisor size metadata is keyed by the remote-native 40-hex SHA-1 identity because that is the only identity known for an unresolved foreign blob. The size is scalar metadata only.

No native SHA-1 is padded, translated, or promoted into a repository-visible 64-hex SHA-256 object ID. If the blob is eventually materialized, the normal importer derives the real local SHA-256 from content and removes the stale promise/size state.

## Compatibility

Phase278 does not change the filter threshold semantics established against Git 2.55, does not change object ordering, and does not introduce a new output protocol.

Existing deferrals remain unchanged on this stack, including combinations whose framing is owned by separate sibling phases. The only new capability is classifying an unresolved promised blob when a trusted size is already available.

## Tests

`tests/test_phase278.py` covers:

- exact-threshold filtering from trusted metadata;
- keeping a smaller promised blob;
- missing-record filtering using native promisor identity;
- strict failure when trusted size is absent;
- non-blob missing objects remaining unaffected;
- ordered inventory membership without reordering;
- preservation of the unresolved native/SHA-256 identity boundary;
- explicit guards against single-object and batch content fetching.
