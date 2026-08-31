# Phase 339 — Content-authenticate LMAP-backed incremental haves

Phase339 hardens the Phase333–338 incremental packfile-URI negotiation boundary.
A structurally valid, checksummed Git LMAP file is compatibility metadata; its
checksum proves the bytes are internally intact, but by itself it does not prove
that a claimed native SHA-1 is the actual Git SHA-1 counterpart of the mapped
local SHA-256 object graph.

That distinction matters before `have` is sent. A false `have` may cause an
upload-pack server to omit objects that pygit does not actually possess under that
native identity.

## New trust boundary

For every existing remote-tracking tip that already passes Phase333's complete
LMAP-coverage checks, the planner now performs a second semantic verification:

1. read the real local SHA-256 commit/tree/blob closure;
2. reuse `NativeExporter`, the same native serializer used by pygit's SHA-1 Git
   interoperability path;
3. recursively reconstruct the canonical native Git object graph;
4. derive each native object id from `SHA1("<type> <size>\\0" + payload)`;
5. require the recomputed native tip to equal the LMAP tip;
6. require every local object in the planned closure to recompute to exactly the
   native SHA-1 claimed by LMAP.

Only then can the tip enter `PackfileUriIncrementalState.haves` and only then is
the closure exposed as `known_native_to_local` for the importer side.

## Why NativeExporter is reused

Commit and tree objects cannot be authenticated by simply SHA-1 hashing their
local SHA-256 representation. Local commits contain SHA-256 tree/parent ids and
local trees contain SHA-256 child ids, while the native Git form contains SHA-1
references. Reconstructing the native form therefore requires walking the mapped
closure and translating references according to real object content.

`NativeExporter` already implements that canonical conversion, including Git tree
mode normalization and commit/tree/blob serialization. Reusing it avoids a second
serializer that could drift from push/export behavior.

## Failure and fallback semantics

A complete LMAP whose mapping is semantically false is treated as insufficient
negotiation evidence:

- no forged native id is sent as `have`;
- no forged pair is returned in `known_native_to_local`;
- the affected tracking ref is listed in `fallback_refs`;
- the caller therefore retains the established full-fetch path.

Missing or unreadable local objects that LMAP claims still fail closed as
repository corruption, preserving Phase333 behavior. Shallow foreign commits still
fall back because their complete native-parent closure is intentionally unresolved.

## SHA-256-native invariants

- local object and ref identities remain full content-derived 64-hex SHA-256;
- remote negotiation identities remain genuine full content-derived 40-hex SHA-1;
- LMAP is evidence only after semantic content authentication;
- no SHA-1 padding, truncation, identifier-text rehashing, surrogate SHA-256, or
  metadata-derived local identity is introduced.

## Git compatibility

Git's current `gitformat-loose` documentation defines LMAP v1 under
`$GIT_DIR/objects/object-map/map-*.map` as the compatibility mapping used when
`compatObjectFormat` is enabled. The format trailer is a checksum of the map file;
Phase339 deliberately adds the repository-side semantic check needed before pygit
uses an LMAP entry as protocol negotiation evidence.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- exact base: Phase338 / PR #315 head
  `77b9f12860ca146503f9e4e280772ff595dc3ca4`
- Phase338 authoritative Tests #2894: success, 2547 passed on Python 3.9 and 3.13
- Phase337 remains occupied by the independent unborn-clone line
- Phase339 was collision-checked immediately before branch creation

## Tests

`tests/test_phase339.py` covers:

- genuine exporter-derived LMAP closure remains eligible for incremental `have`;
- checksummed LMAP with a forged tip SHA-1 falls back;
- checksummed LMAP with a forged dependency SHA-1 falls back even when the tip
  entry itself is genuine;
- forged SHA-1/object-id text is never padded, reused, or rehashed into a
  substitute identity.

The inherited Phase333–338 tests remain authoritative for complete-closure
planning, full-fetch fallback, mapped staging, LMAP persistence, zero-object
incremental completion, guarded CAS ref publication, and native Git pack behavior.

This PR must remain open and unmerged unless explicitly requested.
