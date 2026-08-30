# Phase 301 — Integrated strict protocol-v2 transport hardening

Phase301 consolidates the active protocol-v2 transport hardening siblings into one continuation from the exact-green Phase300 tree and closes two remaining command-envelope gaps.

## Integrated work

The branch cleanly carries forward:

- Phase298 shared Smart HTTP media-type validation for discovery and upload-pack POST responses;
- Phase299 validation that persistent promisor size metadata is keyed only by full remote-native 40-hex SHA-1 object IDs;
- Phase300 strict flush-terminated protocol-v2 fetch response parsing.

Phase301 additionally applies the shared Smart HTTP response validator to the fetch client's private `ls-refs` and `fetch` POST paths, so every upload-pack POST rejects a wrong or missing real HTTP Content-Type before reading response bytes.

## New strict protocol-v2 grammar

Two previously permissive parsers are now fail-closed once protocol v2 is positively identified.

### Capability advertisement

A protocol-v2 capability advertisement must end with exactly one `flush-pkt`.

Phase301 rejects:

- EOF after `version 2` / capability records without a flush;
- `delim-pkt` or `response-end-pkt` as a capability-list terminator;
- any bytes after the final flush;
- malformed UTF-8 capability text.

The existing protocol-v0 fallback remains unchanged: a response that does not positively identify itself as `version 2` still returns the established `None` fallback signal.

### `ls-refs`

A protocol-v2 `ls-refs` response must likewise be a sequence of ref records followed by exactly one `flush-pkt`.

Phase301 rejects:

- EOF before the flush;
- delimiter or response-end terminators;
- trailing bytes after the flush;
- malformed UTF-8 ref records.

Native SHA-1 ref and peeled-object validation remains unchanged.

## Smart HTTP layering

For real HTTP responses, validation now proceeds in layers:

1. discovery must use `application/x-git-upload-pack-advertisement`, otherwise the client returns the existing fallback signal without reading the body;
2. post-negotiation upload-pack responses must use `application/x-git-upload-pack-result`, otherwise they fail before body read;
3. after a valid HTTP envelope, each command parser validates its own pkt-line grammar (`ls-refs`, `object-info`, or `fetch`);
4. malformed command framing remains a hard failure even when the MIME type is correct.

Header-less synthetic response doubles used by older focused unit tests remain supported. Real `urllib` responses expose a header API and therefore use the strict HTTP path.

## Promisor metadata identity

The trusted `sizes` side channel in `.pygit/promisor.json` remains remote metadata and is therefore keyed only by genuine full 40-hex native SHA-1 OIDs.

- abbreviated, overlong, non-hex, and arbitrary keys are rejected;
- a 64-hex local SHA-256 cannot be used as a surrogate metadata key;
- malformed persisted size metadata fails closed during state read;
- no invalid partial update is written after validation failure.

## SHA-256-native invariants

Phase301 does not weaken pygit's repository identity boundary.

- remote protocol identities remain genuine native SHA-1;
- repository-visible objects remain content-derived SHA-256;
- no SHA-1 padding, truncation, translation, or surrogate SHA-256 is introduced;
- no metadata-only native-to-local object mapping is created;
- object-info remains scalar metadata only;
- no additional content materialization path is introduced.

## Regression coverage

The integration branch retains Phase298 and Phase299 focused tests and adds `tests/test_phase301.py` covering:

- truncated / response-end / trailing capability advertisements;
- explicit protocol-v0 fallback preservation;
- truncated / response-end / trailing `ls-refs` responses;
- fetch-client `ls-refs` MIME failure before body read;
- fetch POST MIME failure before body read;
- correct MIME plus malformed fetch framing still failing after body read;
- a complete three-response Smart HTTP discovery → ls-refs → fetch exchange;
- native Git stateless-rpc protocol-v2 capability advertisement and `ls-refs` framing;
- preservation of valid ACK-only strict fetch parsing.

The complete historical pytest matrix on Python 3.9 and 3.13 remains the authoritative regression gate.
