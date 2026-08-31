# Phase 332 — Read and validate Git loose object maps

Phase330 can publish Git-compatible LMAP v1 files under
`objects/object-map/map-<sha256>.map`. Phase332 adds the inverse trust boundary:
pygit can now consume those files without treating compatibility identities as
unverified metadata.

## Scope

`pygit.loose_object_map` now provides:

- `decode_loose_object_map()` — validates and decodes one LMAP v1 file;
- `read_loose_object_maps()` — loads all repository loose-object maps and rejects
  contradictions across immutable generations;
- `lookup_native_sha1()` — maps one full local SHA-256 object id to its verified
  compatibility/native SHA-1 identity;
- `lookup_local_sha256()` — maps one full native SHA-1 identity back to the local
  SHA-256 identity.

The decoder verifies the LMAP signature/version, SHA-256-storage and
SHA-1-compatibility format identifiers, section offsets, canonical minimum
shortened-name widths, table padding, strict sort order, metadata type, the
compatibility-order permutation, and the SHA-256 trailer before exposing any
mapping.

Repository loading additionally requires `map-<checksum>.map` to match the
validated trailer checksum and rejects both SHA-1→different-SHA-256 and
SHA-256→different-SHA-1 contradictions across map files.

## SHA-256-native invariant

Phase332 never derives one object identity from the other. A lookup succeeds
only when a validated Git object-map explicitly contains the pair. Missing
entries return `None`; no SHA-1 padding, truncation, re-hashing of local object
ids, or surrogate compatibility id is synthesized.

This provides the safe primitive needed for a later incremental-fetch phase to
translate already-present local SHA-256 tips into real native SHA-1 `have`
arguments.

## Coordination

Phase331 was already occupied by the independent
`phase331-orchestrate-unborn-empty-clone` branch, so this work deliberately uses
Phase332 and stacks directly on Phase330 exact-green head
`70b725006e7c5baa19ee74353b6c688a1b778865`.

The PR remains open and must not be merged automatically.
