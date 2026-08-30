# Phase 305: strict protocol-v2 fetch state machine

Phase 305 upgrades the protocol-v2 `fetch` response parser from a loose section
collector into a command-specific state machine aligned with Git's current fetch
grammar and observed native stateless-rpc behavior.

## Motivation

Phase 300 made the outer fetch response fail closed: a response must end in one
`flush-pkt`, cannot use `response-end-pkt` as the command terminator, and cannot
carry trailing bytes after the flush. Phase 301 then added Smart HTTP MIME
validation before fetch response bytes are read.

The section parser itself was still deliberately permissive. It accepted section
headers in arbitrary order, delimiter packets before or after inappropriate
sections, acknowledgments state transitions that Git's grammar does not permit,
and duplicate metadata records that could silently overwrite or duplicate state.

Git 2.55 documents the fetch response as either:

```text
acknowledgments flush-pkt
```

or a strictly ordered pack-producing stream:

```text
[acknowledgments delim-pkt]
[shallow-info delim-pkt]
[wanted-refs delim-pkt]
[packfile-uris delim-pkt]
packfile flush-pkt
```

The documentation also requires a response to a request containing `done` to
omit the acknowledgments section. If `wait-for-done` is requested, the server
must not send `ready`; it must wait for a later `done` before sending the
packfile.

## Response state machine

Phase 305 enforces the section order:

1. `acknowledgments`
2. `shallow-info`
3. `wanted-refs`
4. `packfile-uris`
5. `packfile`

Sections remain optional where the protocol permits them, but they cannot move
backwards, repeat, or appear after `packfile`. `packfile-uris` remains explicitly
unsupported because pygit does not request or implement that feature.

Delimiter packets now have structural meaning instead of merely resetting the
current section. The parser rejects:

- a delimiter before the first section;
- repeated delimiters;
- a delimiter immediately before the final flush;
- a delimiter after `packfile`;
- non-acknowledgment section streams that never reach `packfile`.

An acknowledgments-only response may end directly in the final flush. If
`ready` is present, the same response must continue to `packfile`, matching the
meaning of `ready` in the fetch protocol.

## Acknowledgment state

The parser now rejects:

- duplicate `NAK`;
- `NAK` mixed with `ACK` or `ready`;
- duplicate `ready`;
- `ACK` after `ready`;
- duplicate `ACK <oid>` records;
- an empty acknowledgments section.

A valid negotiation response may contain `NAK`, one or more ACKs, or ACKs/ready
when the server is advancing to a pack-producing response.

## Shallow and wanted-ref metadata

`shallow-info` now rejects duplicate `shallow` and duplicate `unshallow`
identities, plus conflicting shallow/unshallow records for the same native OID.

`wanted-refs` rejects duplicate ref names instead of silently overwriting an
earlier mapping.

All these identities remain genuine remote-native full 40-hex SHA-1 values.

## Textual pkt-line records

The old fetch parser used unrestricted `rstrip(b"\n")`. Phase 305 instead accepts
Git's two interoperable forms for textual pkt-line records:

- no terminal LF;
- exactly one terminal LF.

It rejects embedded/repeated LF, CR/CRLF, NUL, and invalid text encoding in
structural fetch records. Packfile sideband payloads remain binary and are not
subject to textual normalization.

## Request/response mode contract

`build_fetch_request()` now rejects the nonsensical combination of both `done`
and `wait-for-done`.

The client validates the response against the request mode:

- a `done` fetch must omit acknowledgments and contain a packfile;
- a `wait-for-done` negotiation must contain acknowledgments, must not contain
  `ready`, and must not send a packfile before the client sends `done`.

This keeps request semantics close to the response parser without adding any
stateful server dependency.

## Native compatibility

The focused regression suite includes a real `git upload-pack --stateless-rpc`
probe for both modes:

- `done`: native Git returns a `packfile` section followed by `flush-pkt`, with
  no acknowledgments section;
- `wait-for-done` with a common `have`: native Git returns an
  acknowledgments-only response with `ACK <oid>` and `flush-pkt`, without
  `ready` or pack data.

These probes run again under the CI runner's native Git version.

## SHA-256-native boundary

Phase 305 changes transport validation only.

- fetch protocol OIDs remain remote-native 40-hex SHA-1;
- repository-visible object identities remain content-derived SHA-256;
- no SHA-1 padding, truncation, or translation is introduced;
- no surrogate SHA-256 identity is created;
- no metadata-only native-to-local mapping is created;
- no additional object materialization or content-fetch path is introduced.

## Coordination

Phase 305 is based on Phase304 / PR #280 exact-green head
`f0093f61d2e18aafd2279cebcd01fdee156c1207` (Tests #2659: Python 3.9 and
3.13 both 2250 passed on Git 2.55.0).

Phase303 is independently occupied by strict capability/`ls-refs` record grammar
work in `pygit/protocol_v2.py`. Phase304 stays in the object-info parser. Phase305
therefore confines production changes to `pygit/protocol_v2_fetch.py` and does
not overwrite either sibling line.
