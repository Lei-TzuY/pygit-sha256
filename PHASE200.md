# Phase 200 — Protocol v2 `fetch` transport foundation

Phase 200 extends the protocol-v2 work from Phase 199 from reference discovery
into real upload-pack transfer framing, while deliberately keeping normal
porcelain fetches on the established protocol-v0 path until the v2 transport is
fully integrated with every fetch policy seam.

## Scope

`pygit.protocol_v2_fetch` adds:

- protocol-v2 `fetch` request construction;
- deterministic `want` / `have` de-duplication and SHA-1 boundary validation;
- `no-progress` and `ofs-delta` requests compatible with pygit's current pack
  parser;
- deliberate omission of `thin-pack`, because pygit does not yet thicken deltas
  against external base objects;
- parsing of sectioned protocol-v2 fetch responses;
- acknowledgment (`ACK`, `NAK`, `ready`) parsing for future negotiation-only
  support;
- `shallow-info` and `wanted-refs` parsing;
- sideband pack reassembly with progress suppression and fatal channel handling;
- explicit rejection of unexpected `packfile-uris` instead of silently ignoring
  an unsupported transfer path;
- `SmartHttpV2FetchClient`, which performs the v2 capability → `ls-refs` →
  `fetch` smart-HTTP sequence and returns the existing `FetchResult` shape;
- a clean `None` fallback signal when a server ignores `Git-Protocol: version=2`
  and answers using protocol v0.

## Protocol compatibility

The implementation follows Git's protocol-v2 fetch grammar:

1. `fetch` is a command request introduced by `command=fetch` and a delimiter.
2. `want`, `have`, `done`, `no-progress`, `include-tag`, and `ofs-delta` are
   command arguments.
3. The response is split into named sections separated by delimiter packets.
4. Pack data in the `packfile` section is always sideband multiplexed; channel 1
   carries pack bytes, channel 2 progress, and channel 3 fatal errors.
5. When `done` is sent, the server is expected to complete negotiation and send
   a packfile rather than requiring another round trip.

The parser already understands acknowledgment-only responses. That is the
necessary wire-level prerequisite for implementing Git-style
`fetch --negotiate-only` in a later phase without pretending protocol v0 can
provide it.

## SHA-256-native boundary

Protocol v2 changes only the transport dialogue. Remote object IDs remain native
Git SHA-1 values at the HTTP/upload-pack boundary and are validated as 40-hex
identifiers. Received pack objects are handed to the existing native pack
parser/import pipeline, which remains responsible for converting them into
pygit's SHA-256-native repository identity.

No local ref, index, object serialization, native-map, FETCH_HEAD, or repository
object format changes in this phase.

## Deliberate integration boundary

Normal `pygit fetch` is **not** switched to v2 yet. Phases 183–198 accumulated
many command-scoped behaviors by wrapping the established `SmartHttpClient`
transport: pruning, tag policy, direct URLs, multi-remote orchestration, atomic
updates, dry-run, prefetch, refetch, and negotiation restrictions/includes.

Routing only some of those through a new client would create subtle semantic
regressions. Phase 200 therefore isolates and tests the wire transport first;
the next integration phase can introduce one compatibility adapter and verify
all existing fetch controls against it.

## Tests

`tests/test_phase200.py` covers:

- fetch command framing, option selection, de-duplication, and invalid OIDs;
- acknowledgment-only responses;
- shallow and wanted-ref sections;
- sideband pack reconstruction and ignored progress;
- fatal sideband errors;
- explicit packfile-URI rejection;
- the complete smart-HTTP v2 capability / `ls-refs` / `fetch` exchange;
- clean protocol-v0 fallback signaling.
