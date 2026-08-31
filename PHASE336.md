# Phase 336 — Persist incremental compatibility object maps

Phase 336 closes the lifecycle gap left after Phase 334's mapped incremental
packfile-URI fetch: every newly fetched native SHA-1 -> local SHA-256 identity is
now persisted as immutable Git LMAP v1 compatibility metadata before remote-
tracking refs are advanced.

## Why this is necessary

Phase 333/334 can advertise a native SHA-1 `have` only when the current local
SHA-256 tracking tip and its reachable closure have verified compatibility
mappings. Without persisting the identities learned during the latest fetch, the
next fetch cannot safely reuse the newly published tip and must fall back to a
larger/full graph request.

Phase 336 makes successful incremental fetches self-feeding: a newly published
tracking tip is immediately backed by durable native SHA-1 compatibility metadata
for the next negotiation round.

## Transaction ordering

The mapped incremental repository transaction now runs:

1. validate publication plan and snapshot mutable repository state;
2. download and verify external packfile URIs;
3. stage/import the native object graph into content-derived local SHA-256 objects;
4. publish the staged native SHA-1 -> local SHA-256 mapping as a Git LMAP v1 file;
5. certify the requested fetched roots;
6. acquire the existing Phase 326 publication guard locks;
7. re-check mutable state and publish refs through expected-old CAS;
8. release guard locks.

The LMAP publication is intentionally before ref publication. `map-<checksum>.map`
files are immutable and content-addressed, so a later certification/CAS failure may
leave only verified compatibility metadata for already-written immutable objects.
It cannot expose a partially advanced history. Conversely, if map publication
fails, no ref publication is attempted.

## Git compatibility

Git documents loose-object compatibility maps at
`$GIT_DIR/objects/object-map/map-*.map` using the LMAP v1 format. Phase 330/332
already implement strict Git-compatible encoding, checksum validation, algorithm
identifiers, table ordering, shortened-name widths, metadata type, and cross-file
conflict detection. Phase 336 reuses that established writer rather than creating a
private pygit map format.

## SHA-256-native invariants

- remote negotiation/object identities remain genuine full 40-hex SHA-1 values;
- local repository identities remain genuine content-derived full 64-hex SHA-256;
- LMAP records only mappings produced by the validated native-object import path;
- local objects are re-read and re-hashed before LMAP publication;
- no SHA-1 padding, truncation, re-hashing of identifiers, or surrogate SHA-256 is
  permitted.

## Failure semantics

- download/staging failure: no LMAP and no ref publication;
- LMAP failure: no certification or ref publication;
- certification failure: an immutable verified LMAP may remain, but no ref moves;
- stale CAS / publication race: imported objects and immutable LMAP may remain
  unreachable, while refs retain their previous values.

These leftovers are safe because both objects and map generations are
content-addressed immutable state.

## Tests

`tests/test_phase336.py` covers:

- exact `LMAP -> certification -> refs` ordering;
- LMAP failure aborting before certification/ref mutation;
- a real staged native commit becoming bidirectionally lookupable after the
  transaction;
- ref publication failure leaving no tracking ref but retaining only a valid
  immutable compatibility map.
