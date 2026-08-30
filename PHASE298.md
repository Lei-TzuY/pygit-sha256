# Phase 298 — Validate shared protocol-v2 Smart HTTP envelopes

Phase298 extends Phase296's fail-closed Smart HTTP media-type validation from the object-info-specific POST path to the shared protocol-v2 query transport used for capability discovery and `ls-refs`.

It also removes the duplicate object-info media-type parser and makes object-info reuse the same normalization and validation helpers as the shared v2 transport.

## Git Smart HTTP behavior

Git's HTTP protocol defines service-specific media types for smart transport.

For `GET /info/refs?service=git-upload-pack`, a smart response uses:

`application/x-git-upload-pack-advertisement`

Git documents that a client should fall back when another content type is returned. Phase298 therefore preserves pygit's existing protocol fallback boundary: a real discovery response with a missing or mismatched media type returns the established `None` fallback signal without reading or parsing its body.

For `POST /git-upload-pack`, the response media type is:

`application/x-git-upload-pack-result`

Once protocol v2 has been negotiated, a missing or mismatched result media type is treated as malformed transport and raises `ValueError` before response bytes are read.

Media-type comparison is case-insensitive and ignores optional parameters such as `charset=...`.

## Compatibility with existing test transports

Older focused tests use deliberately minimal response doubles that expose only `read()` and the context-manager protocol. They do not model HTTP headers.

Phase298 continues to accept those header-less doubles so existing unit isolation is preserved. A real `urllib` response exposes a header API even when the `Content-Type` field itself is absent, allowing missing real headers to remain distinguishable from legacy synthetic responses.

## Shared implementation

`pygit.protocol_v2` now owns the upload-pack advertisement/request/result media-type constants plus shared helpers for:

- detecting a real response header API;
- normalizing `Content-Type`;
- comparing one response against an expected media type;
- rejecting malformed post-negotiation Smart HTTP envelopes before body parsing.

`SmartHttpV2ObjectInfoClient` reuses those helpers instead of maintaining a second copy of the same HTTP-envelope logic.

## SHA-256-native invariants

Phase298 changes only HTTP envelope validation.

- remote protocol identities remain genuine native full 40-hex SHA-1 OIDs;
- local repository object identities remain content-derived SHA-256;
- no SHA-1 padding, translation, or surrogate SHA-256 is introduced;
- no metadata-only native-to-local identity mapping is created;
- no content materialization is added;
- promisor size metadata remains scalar-only and subject to the Phase294/296/297 trust checks.

## Regression coverage

`tests/test_phase298.py` covers:

1. valid discovery advertisement MIME;
2. case-insensitive/parameter-tolerant discovery MIME normalization;
3. wrong discovery MIME returning fallback without reading the body;
4. missing discovery MIME returning fallback without reading the body;
5. header-less legacy discovery compatibility;
6. valid `ls-refs` result MIME and request headers;
7. wrong `ls-refs` result MIME failing before body read;
8. missing `ls-refs` result MIME failing before body read;
9. header-less legacy `ls-refs` compatibility.

The full historical suite, including Phase275's native Git object-info round trip and Phase297's promisor fallback integration, remains required on Python 3.9 and Python 3.13 with the CI runner's Git implementation.

## Deliberate boundary

The shared discovery API still returns its existing `None` fallback signal. Reusing the already-received non-smart discovery body for a dumb-HTTP path would require a larger transport API change and is intentionally left separate from this envelope-validation phase.
