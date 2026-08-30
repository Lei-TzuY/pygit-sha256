# Phase 297 — Integrate stale-client pruning with Smart HTTP object-info envelope validation

Phase297 reconciles two exact-green siblings from the Phase294 line:

- Phase295 / PR #272 restores bounded repository-scoped object-info client lifetime and proves strict pkt-line framing failures evict a bad session and fall back.
- Phase296 / PR #273 validates the Smart HTTP `Content-Type` for object-info POST results before response bytes are trusted.

Phase297 uses the Phase296 exact-green head as its base and cleanly reapplies Phase295's stale-client pruning. Neither validated sibling is force-updated.

## Combined behavior

For every non-empty promisor size refresh, pygit computes the current effective `(remote, URL, ordered server options)` set and prunes cached clients whose configuration is no longer active.

An unchanged current client remains reusable, including Phase291's stable unsupported-`object-info` capability cache. Real transport/protocol/query failures still evict only the exact failed client.

The object-info HTTP path now also distinguishes two failure layers:

1. **HTTP envelope failure** — a real response with a missing or mismatched `Content-Type` is rejected before `read()`; the resulting `ValueError` evicts that remote's cached client and allows fallback.
2. **Protocol framing failure** — a response with the correct upload-pack result media type is read, but Phase294 still rejects an incomplete/trailing/non-flush pkt-line envelope; that `ValueError` follows the same eviction/fallback path.

This keeps metadata trust fail-closed without turning an unhealthy promisor remote into a hard stop when another configured promisor can answer safely.

## Git compatibility

Git protocol v2 over HTTP uses the smart HTTP transport. Object-info requests remain ordinary `git-upload-pack` POST requests and Phase296 requires `application/x-git-upload-pack-result` on real HTTP responses. Phase294 still requires a complete flush-terminated object-info command response.

No request grammar is changed by Phase297.

## SHA-256-native invariants

- remote metadata requests use genuine full 40-hex SHA-1 transport OIDs;
- only scalar trusted sizes may be persisted;
- no SHA-1 padding or translation;
- no surrogate/synthetic local SHA-256 identity;
- no native-to-local mapping from metadata-only results;
- no content fetch or local object creation merely to classify `blob:limit`.

## Regression coverage

`tests/test_phase297.py` covers:

1. URL-change stale-client pruning;
2. stale cleanup when no network query is required;
3. server-option change pruning;
4. removed-promisor cleanup without disturbing an active sibling;
5. retention of an unchanged unsupported-capability client;
6. wrong Smart HTTP result MIME failing before body read, evicting the bad client, and falling back;
7. correct MIME with a truncated Phase294 pkt-line envelope failing after body read, evicting, and falling back.

The complete historical suite remains required on Python 3.9 and Python 3.13 with the CI runner's native Git.
