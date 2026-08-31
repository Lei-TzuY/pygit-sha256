# Phase 327 — Smart HTTP packfile-URI repository integration

Phase327 connects the exact-green protocol-v2 packfile-URI transport to the
exact-green repository transaction.  A caller can now run one high-level path
from protocol-v2 negotiation through verified external-pack import and guarded
compare-and-swap ref publication without manually unpacking the Phase318 result.

## Why this phase exists

The preceding phases deliberately separated trust boundaries:

- Phase318 negotiates `packfile-uris`, parses native Git `sideband-all`, and
  returns inline native objects plus external descriptors;
- Phase320 bounds and verifies external HTTP(S) packs;
- Phase321 imports the complete native object graph through an isolated local
  SHA-256 staging store;
- Phase322 certifies requested roots against published content-derived local
  objects and Git object types;
- Phase323 publishes refs through canonical per-ref locks and expected-old CAS;
- Phase324 composes download, staging, certification, and publication;
- Phase325 snapshots the bounded mutable publication surface;
- Phase326 holds repository-wide metadata locks across the final snapshot check
  and target-ref transaction.

Before Phase327, applications still had to take
`V2PackfileUriFetchResult.packfile_uris` and `.objects` and manually feed them to
`execute_packfile_uri_fetch_transaction()`.  That left an avoidable integration
seam where a repository publication plan could be paired with an unrelated
transport result.

## High-level API

`pygit.protocol_v2_packfile_uri_repository` adds:

- `SmartHttpV2PackfileUriRepositoryResult`;
- `fetch_packfile_uris_into_repository()`.

The high-level function:

1. normalizes the requested URI protocol set before transport I/O;
2. calls `SmartHttpV2PackfileUriClient.fetch_with_packfile_uris()`;
3. preserves the transport's explicit `None` signal for a protocol-v0 server;
4. binds every expected/certified native root to the exact advertisement carried
   by this transport result;
5. rejects peeled `refs/tags/*^{}` values as independent fetch roots, matching
   the Phase318/ordinary fetch `want` selection;
6. forwards the exact descriptor tuple and inline native object mapping to the
   Phase326 repository transaction;
7. returns both the original transport result and the complete transaction
   result on success.

No external pack is downloaded by this integration layer itself.  Resource
limits, checksums, PACK parsing, object import, certification, locks, and ref
updates remain delegated to their existing single-purpose boundaries.

## Transport/publication binding

The binding check uses the same fetchable advertisement set as protocol-v2
fetch: `HEAD`, `refs/heads/*`, and non-peeled `refs/tags/*`.  Every key in
`expected_roots` must be a full 40-hex remote-native SHA-1 value present in that
set.  Every publication root must also be declared in `expected_roots` and be
advertised by this exact fetch result.

This is intentionally stricter than merely proving that an object happened to
arrive in one of the packs.  Content verification proves *what* an object is;
request binding additionally proves that the repository publication plan is
about a tip this fetch actually requested from the remote advertisement.

Case aliases that normalize to the same native SHA-1 identity are rejected in
`expected_roots` rather than allowing two spellings to cross the transaction
boundary.

## Timeouts and resource bounds

The Smart HTTP client owns discovery, `ls-refs`, and upload-pack request timeouts.
External URI downloads default to the same `client.timeout`, but callers may set
`external_timeout` independently.  Phase320's established bounds remain
available unchanged:

- maximum bytes per external pack;
- maximum cumulative external-pack bytes;
- maximum descriptor/pack count;
- optional injected external opener for controlled transports/tests.

## Failure model

- invalid URI protocols fail before transport network I/O;
- protocol-v0 discovery returns `None` and does not enter repository mutation;
- a mismatched or unadvertised expected/publication root fails before any
  external URI download or repository transaction work;
- later external-pack, staging, certification, snapshot, lock, or CAS failures
  retain the Phase320-326 failure semantics: no successful partial ref
  publication is exposed, while already-verified content-addressed local objects
  may remain unreachable.

## SHA-256-native invariants

Phase327 does not add a new identity conversion path.

- advertisement tips and fetch wants stay genuine full 40-hex SHA-1 OIDs;
- descriptor 40-hex hashes stay native pack checksums, never object IDs;
- inline/external object mappings stay remote-native until Phase321 imports
  actual Git object content;
- repository objects and published refs stay full content-derived 64-hex
  SHA-256 identities;
- no SHA-1 padding, truncation, surrogate SHA-256, or metadata-derived local
  object identity is introduced.

## Tests

`tests/test_phase327.py` covers:

- exact Phase318 → Phase326 argument/result wiring;
- protocol normalization before transport I/O;
- transport timeout inheritance and independent external timeout override;
- protocol-v0 fallback without repository transaction entry;
- rejection of expected roots not advertised by the exact transport result;
- exclusion of peeled tag values from the requested-root set;
- publication roots that are absent from `expected_roots`;
- case-alias duplicate native roots;
- preservation of 40-hex remote versus 64-hex local identity domains;
- concrete repository/client API requirements.

The complete existing suite remains the compatibility gate.

## Coordination

- latest `main` at phase start:
  `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase326 / PR #302 head
  `e73381c8e606fe118cd515402d1e0ab4ce1b41f3`;
- Phase326 authoritative Tests #2793: success;
- open PRs and branches were rechecked immediately before branch creation;
- no Phase327 branch existed at creation time.

The Phase327 PR is intentionally left open and unmerged.
