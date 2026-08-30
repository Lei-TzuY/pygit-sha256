# Phase 286 — bounded promisor size preflight

Phase285 changed `rev-list --filter=blob:limit=<n>[kmg]` promisor size recovery from one metadata round trip per unresolved blob to one inventory-level `object-info size` query.  That removed N+1 metadata requests, but an arbitrarily large partial clone could still produce an arbitrarily large single protocol request.

Phase286 keeps the same metadata-only design while bounding each protocol-v2 request.

## Behavior

`refresh_promisor_sizes()` now:

- normalizes and de-duplicates full remote-native SHA-1 object IDs exactly as before;
- removes promises whose trusted size is already persisted before any remote request;
- sorts the remaining native IDs deterministically;
- sends at most 256 OIDs in each `object-info size` request;
- persists trustworthy sizes after each successful chunk, so progress is durable even when a later request fails;
- stops issuing additional chunks to a remote after a transport/protocol/query failure and moves to the next configured promisor remote;
- recomputes the still-missing set before trying that next remote;
- never falls back to a content fetch merely to classify `blob:limit`.

A partial metadata response is still allowed at the refresh layer: known sizes are persisted and unresolved IDs remain pending.  The existing `rev-list` caller remains the strict policy boundary and fails before user-visible output if any required blob size is still unavailable.

## Git compatibility

Git protocol v2 `object-info` accepts multiple `oid <object-id>` request lines.  Phase286 does not change the protocol grammar or introduce a new capability.  The 256-OID chunk limit is an internal client resource bound only; it is intentionally invisible to repository semantics and output framing.

The observable `blob:limit` rule remains unchanged: blobs with uncompressed size **at least** the threshold are filtered.

## SHA-256-native boundary

Chunking operates entirely in the remote transport identity domain:

- request OIDs are genuine 40-hex remote-native SHA-1 values;
- only scalar uncompressed sizes are persisted;
- no SHA-1 is padded, translated, or synthesized into a local SHA-256 identity;
- no local object is created and no native-to-local resolution entry is populated;
- repository-visible SHA-256 continues to be derived only from materialized object content.

## Tests

`tests/test_phase286.py` covers:

- deterministic multi-chunk requests;
- skipping already-persisted sizes before chunking;
- stopping a failed remote after its first failed chunk and falling back with the complete remaining set;
- preservation of 40-hex native identities in every chunk;
- rejection of an invalid non-positive internal batch bound.

The full existing test suite remains the compatibility gate for Phase285 behavior and all earlier rev-list/promisor functionality.
