# Phase 90 — strict `verify-pack` pair validation

Phase 90 removes the legacy duplicate binary parser from `pygit.pack_verifier` and makes `verify-pack` reuse the repository's canonical strict pack-index and pack validators.

## Validation pipeline

`verify_packfile(idx_path)` now performs three layers of validation before returning metadata:

1. `parse_index()` validates the complete `.idx` image, including signature/version, fan-out structure, exact length, SHA-256 checksum, canonical object IDs, ordering, CRC records, and offsets.
2. `parse_pack()` validates the complete sibling `.pack`, including signature/version/count, SHA-256 trailer, bounded zlib expansion, object headers, exact entry boundaries, canonical envelopes, typed commit/tree/tag payloads, duplicate IDs, and recomputed SHA-256 identities.
3. The verifier cross-checks the two independently valid images: object sets, per-object offsets, and CRC32 values must agree exactly.

This pair-level step is important because an index can be internally well-formed and checksummed while describing a different pack.

## API compatibility

The existing API and result shape are unchanged:

```python
records = repo.verify_pack(str(idx_path), verbose=True)
# (oid, type_name, size, compressed_size, offset)
```

`verbose` remains accepted for compatibility. Validation is always strict regardless of that flag.

## Resource-safety boundary

The old verifier called `zlib.decompressobj().decompress()` without an output bound over the remainder of the pack. Phase 90 routes decoding through the Phase 66 bounded decompressor, which caps each expansion at `declared_size + 1` before rejecting oversized entries. Regression coverage white-box asserts this bound for an underdeclared 1 MiB payload.

## Scope

This phase does not change the educational SHA-256/non-delta pack schema, `PackReader` random-access behavior, `index-pack`, `unpack-objects`, or `pack-objects`. It only hardens full `.idx`/`.pack` integrity verification and preserves the existing `Repository.verify_pack` contract.
