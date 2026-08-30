# Phase 320 — bounded external-pack batch verification

Phase320 composes Phase319's explicit checksum-verifying downloader across every
`packfile-uris` descriptor in one response, while deliberately staying outside
the repository mutation boundary.

## What changed

`pygit.protocol_v2_packfile_uri_batch.download_packfile_uris()` now:

- materializes and validates the descriptor set before any network request;
- requires at least one descriptor and a configurable positive pack-count bound;
- rejects duplicate advertised pack checksums before network access;
- applies Phase319's HTTP(S), redirect, timeout, per-pack size, pack framing, and
  dual-checksum verification independently to every external pack;
- adds a separate cumulative byte budget across the complete descriptor batch;
- preserves descriptor/download order;
- merges parsed remote-native objects only after each corresponding pack has
  passed integrity verification;
- tolerates the same native object appearing identically in more than one pack,
  but rejects conflicting payloads for one native OID;
- returns no partial batch result if a later external pack fails.

The default limits are 64 packs, 256 MiB per pack, and 512 MiB total.  They are
caller-configurable positive integers.

## Transaction boundary

This phase remains intentionally in-memory.  It does not update refs, write the
SHA-256 object store, create `.keep` files, alter promisor metadata, or mark a
fetch complete.  Network reads that succeeded before a later failure cannot be
undone, but no partial verified set is exposed as a successful batch result.

A later repository-level phase can use this all-verified batch as input to the
Git-style transaction that imports inline and external packs, performs
connectivity checks, updates refs, and rolls back repository mutations on
failure.

## SHA-256-native invariants

Downloaded pack objects remain genuine remote-native SHA-1 identities.  The
40-hex descriptor value remains the native pack checksum, not a repository
object ID.  Phase320 introduces no SHA-1 padding, truncation, translation,
surrogate SHA-256, metadata-derived local object, or native-to-local mapping.
Local SHA-256 identity remains content-derived at the existing importer/store
boundary, which this phase does not cross.

## Tests

`tests/test_phase320.py` covers successful multi-pack merge, deterministic order,
duplicate-checksum and pack-count preflight, cumulative byte limits, identical
object deduplication, conflicting native-object rejection, argument validation,
and empty/non-descriptor rejection.  The inherited Phase319 suite continues to
provide real native-Git pack generation and HTTP checksum/parse coverage.
