# Phase 321 — staged packfile-URI import boundary

Phase321 moves the exact-green Phase320 external-pack batch one step closer to a repository-level fetch transaction without publishing refs prematurely.

## What changes

`pygit.protocol_v2_packfile_uri_stage.stage_packfile_uri_import()` accepts:

- the destination SHA-256 `ObjectStore`;
- unpacked inline remote-native objects from the protocol-v2 response;
- one fully verified `DownloadedPackfileUriBatch` from Phase320.

It validates and merges both native object sets, then imports the complete graph into a temporary isolated SHA-256 object store using the existing `NativeImporter`. Only after every native object and all cross-pack dependencies convert successfully are the staged immutable objects copied into the destination store.

This gives the next transaction phase a stronger invariant: once Phase321 returns, every returned local SHA-256 object is present and readable, while no reference publication has happened yet.

## Failure semantics

Failures during native identity validation, graph traversal, dependency resolution, or conversion happen before any destination object write. A failure during final object publication can leave only valid unreachable content-addressed loose objects. Since refs, HEAD, reflogs, and promisor metadata are not touched, no user-visible ref can point at an incomplete graph.

This mirrors Git's important ordering principle for multi-pack fetches: obtain and validate object material before connectivity/ref publication. Phase321 deliberately does not claim to implement the final ref transaction or keep-file lifecycle.

## SHA-256-native invariants

- inline and external object identities must be genuine full 40-hex remote-native SHA-1 values;
- every native object's SHA-1 is recomputed from its canonical `<type> <size>\0<data>` bytes before staging;
- duplicate native OIDs are accepted only for identical `NativeObject` values;
- local 64-hex SHA-256 identities are produced only by the existing `NativeImporter` / `ObjectStore` content path;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived local identity is introduced;
- refs, HEAD, reflogs, and promisor metadata remain untouched.

## Coordination

Phase321 is based exactly on Phase320 / PR #296 head:

`4389e689eee8e34870da758621629f4815c92bc0`

Phase320 GitHub Actions Tests #2761 completed successfully before Phase321 was created. The `phase321-stage-packfile-uri-imports` branch was checked as free before creation.

## Tests

`tests/test_phase321.py` covers:

- successful cross-pack tree/blob import;
- missing external dependency failure before destination publication;
- forged native SHA-1/content mismatch rejection before publication;
- identical inline/external object deduplication;
- empty combined object-set rejection;
- explicit proof that the local SHA-256 result is content-derived rather than padded native SHA-1.

The complete inherited suite remains the authority for Phase318–320 native Git protocol, packfile-URI descriptor, HTTP download, pack checksum, parser, and batch verification behavior.
