# Phase 336 — Persist incremental compatibility object maps

Phase 336 closes the lifecycle gap left after Phase 334's mapped incremental
packfile-URI fetch: every newly fetched native SHA-1 -> local SHA-256 identity is
persisted as immutable Git LMAP v1 compatibility metadata before remote-tracking
refs are advanced.

## Why this is necessary

Phase 333/334 can advertise a native SHA-1 `have` only when the current local
SHA-256 tracking tip and its reachable closure have verified compatibility
mappings. Without persisting identities learned during the latest fetch, the next
fetch cannot safely reuse the newly published tip and must fall back to a larger or
full graph request.

Phase 336 makes successful incremental fetches self-feeding: a newly published
tracking tip is immediately backed by durable native SHA-1 compatibility metadata
for the next negotiation round.

## Transaction ordering

The mapped incremental repository transaction now runs:

1. validate publication plan and snapshot mutable repository state;
2. accept either a valid inline-only response or download/verify external packfile URIs;
3. stage/import the native object graph into content-derived local SHA-256 objects;
4. publish the staged native SHA-1 -> local SHA-256 mapping as a Git LMAP v1 file;
5. certify the requested fetched roots;
6. acquire the existing Phase326 publication guard locks;
7. re-check mutable state and publish refs through expected-old CAS;
8. release guard locks.

The LMAP publication is intentionally before ref publication. `map-<checksum>.map`
files are immutable and content-addressed, so a later certification/CAS failure may
leave only verified compatibility metadata for already-written immutable objects.
It cannot expose a partially advanced history. Conversely, if map publication
fails, no certification or ref publication is attempted.

## Git compatibility

Git documents loose-object compatibility maps at
`$GIT_DIR/objects/object-map/map-*.map` using LMAP v1. Phase330/332 already
implement strict Git-compatible encoding, checksum validation, algorithm
identifiers, table ordering, shortened-name widths, metadata type, and cross-file
conflict detection. Phase336 reuses that writer rather than creating a private map
format.

Phase334's latest inline-only fallback remains intact: requesting `packfile-uris`
does not force a server to return external descriptors, and a normal terminating
inline `packfile` remains valid. Phase336 persists mappings identically for either
inline-only or URI-offloaded object delivery.

## SHA-256-native invariants

- remote negotiation/object identities remain genuine full 40-hex SHA-1 values;
- local repository identities remain genuine content-derived full 64-hex SHA-256;
- LMAP records only mappings produced by the validated native-object import path;
- local objects are re-read and re-hashed before LMAP publication;
- no SHA-1 padding, truncation, identifier re-hashing, or surrogate SHA-256 is permitted.

## Failure semantics

- download/staging failure: no LMAP and no ref publication;
- LMAP failure: no certification or ref publication;
- certification failure: an immutable verified LMAP may remain, but no ref moves;
- stale CAS/publication race: imported objects and immutable LMAP may remain
  unreachable, while refs retain their previous values.

These leftovers are safe because both objects and map generations are
content-addressed immutable state.

## Tests

`tests/test_phase336.py` covers exact `LMAP -> certification -> refs` ordering,
LMAP failure before mutation, a real inline-only staged native commit becoming
bidirectionally lookupable for the next fetch, and ref-publication failure retaining
only valid immutable compatibility metadata.
