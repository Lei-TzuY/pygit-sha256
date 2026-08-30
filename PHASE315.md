# Phase 315: Preserve protocol-v2 unborn ref metadata

Phase315 preserves the explicit `unborn` sentinel returned by Git protocol-v2
`ls-refs` without changing the long-standing `Advertisement` public shape.

## Motivation

Phase309 already has strict protocol-v2 capability and `ls-refs` parsing, and
`build_ls_refs_request()` already sends the `unborn` argument when the remote
advertises `ls-refs=unborn`. The generic parser also accepts an `unborn` first
field, but the sentinel itself is discarded when the result is projected into
`Advertisement`: callers retain the `HEAD` symref target but cannot distinguish
an explicitly unborn remote HEAD from a merely absent ref.

Git protocol-v2 defines the `unborn` ls-refs feature specifically so a server can
report an empty repository HEAD as:

`unborn HEAD symref-target:<target>`

Phase315 adds an opt-in result channel for that transport metadata while leaving
all historical callers unchanged.

## Implementation

New module: `pygit/protocol_v2_unborn.py`

- `ProtocolV2LsRefsResult`
  - wraps the existing `Advertisement`
  - exposes `unborn: FrozenSet[str]`
- `parse_ls_refs_response_with_unborn()`
  - delegates generic pkt-line, UTF-8, SHA-1, duplicate, symref, and peeled
    validation to the existing strict `parse_ls_refs_response()` first
  - performs a narrow second pass only to preserve and validate the otherwise
    discarded `unborn` sentinel
- `SmartHttpV2UnbornQueryClient.discover_refs_with_unborn()`
  - reuses the existing v2 capability discovery, request builder, server-option
    handling, smart-HTTP MIME validation, and ls-refs response parser
  - returns `None` on protocol-v0 fallback, matching the existing query client

The established `Advertisement` dataclass and `SmartHttpV2QueryClient` behavior
are not modified.

## Unborn-specific trust boundary

An explicit unborn record is accepted only when:

1. the server advertised the `unborn` feature for `ls-refs`;
2. the record describes `HEAD`;
3. the shared parser preserved a non-empty `symref-target` for that HEAD;
4. the unborn record does not carry a `peeled` object identity.

Unknown future ref attributes remain ignored by the shared parser, preserving
its existing forward-compatibility policy.

## Native Git compatibility

Local Git 2.47.3 stateless-rpc probes on an empty bare repository initialized
with `--initial-branch=main` established:

- capability advertisement contains `ls-refs=unborn`;
- an unrestricted `ls-refs` request with `unborn` returns exactly
  `unborn HEAD symref-target:refs/heads/main` followed by flush;
- a `ref-prefix refs/heads/feature` request returns only flush, proving unborn
  metadata is response-scoped rather than an inferred global empty-repository
  flag.

The Phase315 CI test repeats this round trip with the runner's native Git.

## SHA-256-native invariants

Unborn is a ref-state sentinel, not an object identity.

- no zero OID is fabricated for unborn HEAD;
- no 40-hex SHA-1 is invented;
- no SHA-1 is padded, truncated, translated, or converted into a surrogate
  SHA-256;
- no local object is created;
- no `.pygit/promisor.json` state is read or written;
- concrete remote object OIDs, when present, remain genuine native SHA-1 values
  until the existing importer derives local SHA-256 identities from content.

## Coordination

- actual `main` at phase start:
  `bfcbae64e4dc9997b915c16e1aa923a951090083`
- base: Phase313 / PR #289 exact-green head
  `b7a7ff3ce35748207616ef0f991a44a7b09f42ac`
- Phase313 Tests #2710: Python 3.9 / 3.13 both 2325 passed, Git 2.55.0
- Phase312 / PR #288 is independently exact-green filtered-fetch work
- Phase314 is independently occupied by
  `phase314-integrate-filter-shallow-cutoffs` and is intentionally untouched
- Phase315 was rechecked as free immediately before branch creation

## Regression coverage

`tests/test_phase315.py` covers:

- preserving explicit unborn HEAD without fabricating an OID;
- Git-compatible optional terminal LF;
- rejecting unsolicited unborn records when the feature was not advertised;
- rejecting non-HEAD unborn records;
- requiring symref-target metadata;
- rejecting peeled metadata on unborn HEAD;
- leaving ordinary concrete refs unchanged;
- Smart HTTP capability -> ls-refs composition and request emission;
- native Git empty-repository stateless-rpc behavior;
- native prefix filtering removing unborn HEAD from the actual response set.

Full GitHub Actions Python 3.9 / 3.13 matrix is required before Phase315 is
considered exact-green.
