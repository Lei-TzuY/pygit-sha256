# Phase 330 — Git-compatible SHA-256/SHA-1 loose object maps

Phase 329 can fetch a configured SHA-1 remote into pygit's SHA-256-native object
store and publish remote-tracking refs with local SHA-256 CAS. The remaining
interoperability gap is durable knowledge of the verified native SHA-1 identity
for each imported local SHA-256 object.

Phase 330 introduces a Git-compatible persistence boundary instead of inventing a
pygit-private JSON mapping format.

## Native Git format

Current Git documents loose compatibility mappings at:

```
$GIT_DIR/objects/object-map/map-*.map
```

using the binary **LMAP v1** format when `extensions.compatObjectFormat` is in
use. The first algorithm in a SHA-256 repository is the main/storage SHA-256
format (`s256`), followed by compatibility SHA-1 (`sha1`). The file contains
sorted shortened-name tables, complete object-name tables, metadata/order maps,
and a trailer hashed with the main algorithm.

`pygit.loose_object_map.encode_loose_object_map()` follows that layout directly:

- signature `LMAP`, version 1;
- two object formats: SHA-256 then SHA-1;
- Git's minimum unambiguous **byte** prefix lengths;
- Git's minimal NUL padding so full-name and 32-bit tables are aligned;
- storage table sorted by local SHA-256;
- compatibility table sorted by native SHA-1;
- metadata type 1 (`loose object`);
- compatibility-order table mapped back to storage-table positions;
- SHA-256 trailer over all preceding bytes.

## Publication boundary

`publish_staged_loose_object_map()` accepts only Phase321's
`StagedPackfileUriImport`. Before writing a map it re-reads every mapped local
object through the repository ObjectStore and rechecks its content-derived
SHA-256 identity.

The resulting LMAP file is immutable and content-addressed by its SHA-256 trailer:

```
objects/object-map/map-<sha256>.map
```

Publication uses a temporary file plus an exclusive hard-link. An already
published byte-identical map is idempotent; different bytes at the same
checksum-derived path are treated as corruption and fail closed.

This phase deliberately does **not** mutate refs, HEAD, reflogs, shallow state,
or promisor state. Like Phase321's immutable object publication, an object-map
file may safely exist before a ref points at the mapped objects. That property
lets a later phase compose map publication into the fetch transaction without
making the ref transaction less safe.

## Hash-domain invariant

The mapping is explicit and never synthesizes one hash from the other:

- compatibility/native identity: complete lowercase 40-hex SHA-1;
- repository/storage identity: complete lowercase 64-hex SHA-256;
- local SHA-256 is revalidated from stored object content;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived identity.

The SHA-1 side remains the identity that Phase321 verified against the native Git
object envelope before conversion. Trees and commits may have different local
serialized bytes after their embedded references become SHA-256, so Phase330
correctly persists the verified mapping rather than attempting to recompute the
remote SHA-1 from converted local bytes.

## Tests

`tests/test_phase330.py` checks:

- LMAP v1 signature, version, algorithm identifiers, offsets and trailer;
- Git-compatible alignment semantics;
- minimum unambiguous byte-prefix sizing;
- SHA-1 compatibility-order to SHA-256 storage-order mapping;
- metadata type 1 for loose objects;
- content-addressed, idempotent publication;
- local SHA-256 object revalidation before publication;
- strict 40-hex SHA-1 / 64-hex SHA-256 domain validation;
- rejection of ambiguous local aliases and empty mappings.

## Next step

Phase 331 should compose this immutable LMAP publication into the Phase324-329
named-remote fetch transaction and then add a reader/lookup path for deriving
safe native SHA-1 `have` values from existing local SHA-256 tracking history.
That integration should preserve refs as the final mutable commit point and must
avoid advertising a native `have` unless its compatibility mapping is verified.
