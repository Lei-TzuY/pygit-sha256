# Phase 300 — Enforce complete protocol-v2 fetch response framing

Phase300 hardens `pygit.protocol_v2_fetch.parse_fetch_response()` so a protocol-v2 `fetch` response is accepted only when its command-specific pkt-line envelope is complete.

## Git protocol-v2 behavior

Current Git protocol-v2 defines fetch output as either:

- an acknowledgments section followed by `flush-pkt`; or
- zero or more optional sections separated by `delim-pkt`, followed by a packfile section and a final `flush-pkt`.

`response-end-pkt` is not a valid terminator for the fetch command grammar.

Phase300 therefore:

- requires a final `flush-pkt`;
- rejects EOF before the final flush;
- rejects `response-end-pkt` instead of treating it as success;
- rejects any bytes after the final flush packet;
- preserves existing section parsing, duplicate-section checks, ACK/NAK validation, shallow/wanted-ref parsing, sideband handling, and pack validation.

This is deliberately command-specific framing validation. It does not change the shared Smart HTTP MIME work being developed independently in Phase298.

## Why

Before Phase300, `parse_fetch_response()` stopped successfully on either `flush-pkt` or `response-end-pkt`, and EOF without either terminator was also accepted. A valid-looking prefix followed by truncation or trailing bytes could therefore be mistaken for a complete trusted fetch response.

Phase300 makes the final flush part of the trusted fetch envelope, matching the same fail-closed direction used for `object-info` response framing in Phase294.

## SHA-256-native invariants

No identity or object-import semantics change.

- protocol-v2 transport OIDs remain genuine remote-native 40-hex SHA-1 identities;
- repository-visible local objects remain content-derived SHA-256;
- no SHA-1 padding, translation, surrogate SHA-256, or metadata-only native-to-local mapping is introduced;
- no additional fetch/materialization path is introduced;
- pack bytes are still parsed only through the existing importer boundary.

## Regression coverage

`tests/test_phase300.py` covers:

1. EOF without `flush-pkt` is rejected;
2. `response-end-pkt` is rejected;
3. trailing bytes after `flush-pkt` are rejected;
4. exact flush-terminated acknowledgment responses remain accepted;
5. exact flush-terminated packfile responses remain accepted;
6. a real native Git protocol-v2 `upload-pack --stateless-rpc` fetch is flush-terminated and remains parseable.

The native probe runs against the Git version installed on the CI runner and constructs a real repository and pack transfer rather than synthesizing only parser input.

## Coordination

- Phase297 / PR #274 is the exact-green base used for this branch;
- Phase298 is independently occupied by shared Smart HTTP envelope validation and is intentionally not modified;
- Phase299 became independently occupied by promisor-size native-OID validation before this branch was created;
- Phase300 was confirmed free immediately before branch creation.
