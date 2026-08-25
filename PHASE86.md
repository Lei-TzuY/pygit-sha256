# Phase 86: strict bounded `PackReader` reads

Phase 86 hardens the runtime random-access pack reader. Earlier pack-import validation already bounded decompression and Phase 69 made `.idx` parsing strict, but `PackReader.read_object()` still used an independent permissive path. This phase removes that gap without turning lightweight index-only queries into full pack scans.

## Runtime validation

`PackReader` continues to parse only the sibling `.idx` at construction time, so `has_object()` and `get_shas()` remain index-only operations. On the first actual object read, the paired `.pack` image is validated for:

- `PACK` signature and version 2;
- object count matching the validated index;
- SHA-256 trailer checksum;
- every indexed offset falling inside the pack payload.

For the requested object, the reader derives the exact entry boundary from the next indexed offset (or the pack trailer for the final entry), then:

1. parses the pack varint with strict overflow/truncation checks;
2. rejects unsupported object type IDs instead of treating them as blobs;
3. uses bounded zlib decompression capped at the declared size plus one byte;
4. requires the compressed stream to end exactly at the indexed entry boundary;
5. verifies the index CRC32 for that entry;
6. validates the canonical typed object envelope and recomputes its SHA-256 object ID;
7. requires that recomputed ID to equal the requested/indexed OID before deserializing.

This preserves random-access behavior while preventing an under-declared compressed entry from expanding without a bound in memory.

## Compatibility boundary

The implementation remains specific to pygit's educational SHA-256, non-delta pack schema. It does not add native Git delta-object, large-offset, or alternate index support.

## Regression coverage

`tests/test_phase86.py` covers valid multi-entry reads plus pack checksum/count corruption, offset corruption, unknown type IDs, CRC mismatches, wrong indexed OIDs, typed-payload validation, and a white-box assertion that under-declared entries are decompressed with the expected `declared_size + 1` maximum output bound.
