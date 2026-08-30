# Phase296: validate object-info smart-HTTP response type

Phase294 made the protocol-v2 `object-info size` pkt-line response envelope strict.
Phase296 adds the HTTP-layer envelope check before any response body is trusted.

## Behavior

`SmartHttpV2ObjectInfoClient` now validates the media type of a real smart-HTTP
`git-upload-pack` POST response before reading or parsing the body.

- expected media type: `application/x-git-upload-pack-result`
- media-type comparison is case-insensitive
- optional media-type parameters are ignored for comparison
- a missing `Content-Type` on a real HTTP response is rejected
- a mismatched type such as `text/html` or `text/plain` is rejected before
  `response.read()`
- the error remains a `ValueError`, so the existing promisor refresh path treats
  it as a malformed/bad client response, evicts that client, and may fall back to
  another configured promisor remote
- after a valid HTTP media type, Phase294's complete flush-terminated pkt-line
  validation still applies unchanged

Older focused unit-test doubles in the repository sometimes expose only
`read()` and no HTTP header API at all. Those synthetic objects remain accepted
for test compatibility. Real `urllib` HTTP responses expose headers even when
the actual `Content-Type` field is absent, so production traffic always takes
the strict validation path.

## Git compatibility

Git's Smart HTTP protocol defines the `git-upload-pack` request and response
media types as:

- request: `application/x-git-upload-pack-request`
- response: `application/x-git-upload-pack-result`

Phase296 keeps the existing request header and now verifies the matching result
media type before accepting protocol-v2 object metadata. This prevents an HTML
login/proxy/error body or another non-Git response from entering the trusted
pkt-line parser.

The existing Phase275 native Git `object-info size` stateless-rpc test remains
the command-grammar compatibility probe; Phase296 changes only the HTTP response
envelope around that same Git command.

## SHA-256-native boundary

No object identity behavior changes.

- object-info requests still contain genuine remote-native 40-hex SHA-1 OIDs
- only scalar uncompressed sizes may be returned and persisted
- no SHA-1 padding or translation is introduced
- no surrogate local SHA-256 is created
- no local object or native-to-local mapping is created
- no object-content fetch is introduced

## Tests

`tests/test_phase296.py` covers:

- exact upload-pack result media type
- case-insensitive media type with parameters
- wrong media type rejected before body read
- missing media type rejected before body read
- valid media type still subject to Phase294 flush framing
- compatibility with older header-less synthetic response doubles

## Coordination

Phase295 became occupied independently while this work was in progress. The
HTTP validation change was therefore cleanly rebuilt as Phase296 from the exact
Phase294 green head rather than opening a conflicting Phase295 PR or overwriting
another worker's branch.

PR remains intentionally open and unmerged.
