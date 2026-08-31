# Phase 334 — Integrate mapped incremental packfile-URI fetch

Phase333 established when a local SHA-256 remote-tracking tip is safe to expose as
a genuine remote-native SHA-1 `have`. Phase334 connects that read-only decision to
the real fetch/import boundary without allowing negotiation and importer knowledge
to drift apart.

## Core invariant

A protocol-v2 server may omit any object reachable from a `have`. Therefore these
two values are one logical capability and must travel together:

1. `PackfileUriIncrementalState.haves` — native SHA-1 commit tips sent over the
   wire;
2. `PackfileUriIncrementalState.known_native_to_local` — the validated complete
   local closure supplied to `NativeImporter(..., known=...)`.

Sending only (1) is unsafe: a valid server can omit an old tree/blob reused by a
new commit, leaving an importer that knows only fetched objects unable to resolve
the new graph. Supplying only (2) has no transport benefit and is unnecessary.

## Known-aware staging

`stage_packfile_uri_import()` now accepts optional `known_native_to_local`.

Before a known identity can satisfy an omitted dependency, the destination store
must prove that the mapped full 64-hex SHA-256 object exists, is readable, and
still hashes to that exact identity. Hash-domain syntax remains strict and no
mapping is synthesized.

Fetched bytes remain authoritative. If a native OID appears both in the fetched
set and the known map, staging imports the fetched bytes rather than skipping
them, then requires the content-derived local SHA-256 result to equal the known
mapping. Stale compatibility metadata therefore cannot mask contradictory remote
content.

Known-only objects are not copied through the temporary staging store and are not
reported as newly published local objects. They merely satisfy dependencies of
newly fetched native objects.

## Incremental repository transaction

`execute_incremental_packfile_uri_fetch_transaction()` reuses the exact
Phase324-326 repository transaction ordering and private guard helpers:

1. preflight publication plan;
2. snapshot bounded mutable state;
3. download/verify all external packs;
4. known-aware isolated SHA-256 staging;
5. root certification;
6. acquire repository metadata guard locks;
7. recheck mutable state;
8. CAS-publish refs last;
9. release locks.

The only semantic difference from the established transaction is step 4's
validated known-object closure.

## Named-remote integration

`fetch_named_remote_incrementally_with_packfile_uris()` is a new explicit entry
point. It does not silently change Phase329's existing explicit-`haves` API.

The new entry point:

1. resolves the configured remote URL;
2. performs protocol-v2 ref discovery;
3. builds the existing Phase328 tracking/ref CAS plan;
4. builds the Phase333 mapped incremental state;
5. sends exactly that state's native `haves` in the Phase318 fetch;
6. binds the transport result to the planned roots;
7. sends the same state's known map into the guarded repository transaction.

If map coverage is absent or incomplete, Phase333 emits no have for that ref and
the request naturally falls back to the established full-fetch behavior. Initial
v0 discovery still returns `None`; a downgrade after successful v2 discovery
fails closed.

## Native Git regression

The Phase334 native test creates two real Git commits with the same tree. It then
runs:

```text
git pack-objects --stdout --revs
<new>
^<old>
```

The resulting native incremental pack contains the new commit while omitting the
old tree/blob reachable from `<old>`. Parsing/staging that pack without known
objects fails as incomplete. Supplying the previously staged old native→local
mapping succeeds, and the new local SHA-256 commit points at the already-present
local SHA-256 tree and parent.

This proves the integration against native Git's actual object omission behavior,
not only a mocked `have` argument.

## SHA-256-native boundary

Phase334 never derives SHA-1 from a local SHA-256 identifier by truncation,
padding, hashing the identifier text, or any surrogate scheme. Every reusable
native identity comes from validated Git LMAP data read by Phase332/333.

New repository-visible SHA-256 identities remain content-derived through
`NativeImporter` and `ObjectStore.write()`. Compatibility mappings are evidence
for already-existing immutable objects, not a second object identity scheme.

## Exact topology

Base: Phase333 / PR #309 exact-green head:

`cdf5f1ba446f0698c59fa8b39a9c9b2610b9511c`

Phase334 changes:

- `pygit/protocol_v2_packfile_uri_stage.py` — validate and consume known mappings;
- `pygit/protocol_v2_packfile_uri_incremental_fetch.py` — new guarded incremental
  transaction + named-remote entry point;
- `tests/test_phase334.py` — native and orchestration regressions;
- `PHASE334.md` — this document.

Phase329's existing named-remote function and Phase324's existing full transaction
remain behaviorally unchanged.

## Verification gate

The authoritative gate is the complete GitHub Actions Python 3.9 / 3.13 suite on
Git 2.55.0. The PR remains open and unmerged until explicitly requested otherwise.
