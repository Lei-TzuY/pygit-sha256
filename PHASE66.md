# Phase 66: pack import hardening

Phase 66 hardens the Phase 64 `index-pack` / `unpack-objects` ingestion path for untrusted pack data without changing the pack format or CLI surface.

## Bounded decompression

Each zlib member is now decoded with an output limit of the pack entry's declared size plus one sentinel byte. If the compressed stream expands beyond that claim, parsing stops immediately with a size-mismatch error instead of first materializing the entire decompressed payload in memory.

This specifically closes the under-declared decompression-amplification case while preserving valid packs and exact multi-entry stream boundary detection.

## Canonical object envelopes

Imported object bytes must exactly equal the canonical `<type> <payload-size>\0<payload>` envelope produced by pygit's object writer. Alternate encodings such as padded decimal sizes are rejected even when the outer pack checksum is valid.

The parser also reuses the typed `hash-object` validation path for tree, commit, and tag payloads. Structurally incomplete typed objects therefore cannot enter the object database merely because their pack checksum and outer envelope are self-consistent.

## Compatibility boundary

The implementation remains intentionally scoped to pygit's educational SHA-256, non-delta pack schema. No claim is made that these files are native Git SHA-1/delta packfiles.
