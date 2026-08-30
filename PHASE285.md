# Phase285: batched promisor size preflight

Phase285 reduces metadata round-trips for `rev-list --filter=blob:limit=<n>[kmg]` in partial-clone repositories.

Phase284 added a lazy metadata-only recovery path for one unresolved promised blob whose trusted size was absent from `promisor.json`. The underlying `refresh_promisor_sizes()` helper already accepts multiple native object IDs and queries protocol-v2 `object-info size` in a batch, but the `rev-list` preflight still called that helper one object at a time.

Phase285 changes the inventory preflight to collect every unresolved promised blob that still lacks a persisted size and submit the whole set to one refresh call. Already-persisted sizes are skipped, repeated inventory identities are deduplicated case-insensitively, and a partial metadata response remains a hard pre-output error for any still-unclassifiable blob.

## Git compatibility

Git protocol-v2 `object-info` is explicitly intended to answer object metadata questions without fetching object contents. Its `size` attribute accepts multiple object IDs in one request, so batching is a transport-efficiency improvement rather than a new wire semantic.

The `blob:limit` membership rule is unchanged: blobs whose uncompressed size is strictly less than the configured limit are retained; blobs whose size is equal to or greater than the limit are filtered.

## SHA-256-native boundary

The batch contains only native 40-hex SHA-1 identities used to talk to the promisor remote. Returned values are scalar sizes only. Phase285 does not synthesize, pad, translate, or persist any fake 64-hex SHA-256 identity. A local SHA-256 object identity still exists only after content is actually materialized and hashed by the local object store.

## Failure behavior

The preflight still runs before line, count, or structured NUL output is emitted. If any promised blob remains without trusted size metadata after the batch refresh, `rev-list` fails with the existing `persistent promisor size metadata is unavailable` error. It does not fall back to fetching object contents and it does not retry each unresolved object individually after a partial batch response.

## Tests

`tests/test_phase285.py` covers:

- one metadata query for several missing promised blobs;
- skipping already-persisted sizes;
- deduplicating repeated native identities;
- strict failure on a partial batch result without per-object retry;
- avoiding a remote client entirely when every size is already persisted; and
- preserving the native SHA-1 / local SHA-256 identity boundary.
