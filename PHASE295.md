# Phase 295 — Integrate stale object-info client pruning after strict response framing

Phase295 reconciles two exact-green siblings that both extend the promisor `object-info size` path from Phase292:

- Phase293 / PR #270 bounds the lifetime of repository-scoped object-info clients when promisor remote configuration changes.
- Phase294 / PR #271 hardens protocol-v2 object-info response framing and rejects incomplete or trailing command envelopes.

Because the siblings touch different production files, Phase295 uses the Phase294 exact-green head as its base and cleanly reapplies the Phase293 cache-lifecycle change without force-updating either validated branch.

## Behavior

For every non-empty `refresh_promisor_sizes()` invocation, the refresh path now computes the current effective promisor configuration as `(remote, URL, ordered server options)` keys and prunes cached clients whose configuration is no longer active.

This means:

- changing a promisor remote URL discards only the old URL session;
- changing configured `serverOption` values discards only the old effective configuration;
- removing a promisor remote drops its cached client while preserving active siblings;
- stale clients are pruned even when the requested size is already persisted and no object-info network query is required;
- unchanged clients remain reusable, including Phase291's stable unsupported-capability negative cache;
- malformed responses rejected by Phase294 still follow the existing `ValueError` failure path, evict the failed client, and allow deterministic fallback to another promisor remote.

Phase292's per-repository refresh lock makes pruning safe with concurrent callers. The short global cache guard is used only for weak-key cache bookkeeping and is not held across network I/O.

## Git compatibility

Phase295 does not change request grammar. Protocol-v2 `object-info size` still uses genuine remote-native full SHA-1 object IDs, while Phase294's exact `info flush-pkt` response framing remains intact.

No content fallback is added. Only scalar size metadata may be persisted.

## SHA-256-native invariants

The remote SHA-1 / local SHA-256 boundary remains strict:

- no SHA-1 padding or translation;
- no surrogate or synthetic SHA-256 identity;
- no native-to-local identity mapping from size metadata;
- no local object creation from metadata-only refresh;
- no content materialization merely to classify `blob:limit`.

## Regression coverage

`tests/test_phase295.py` covers:

1. URL-change pruning and replacement;
2. stale cleanup when no network query is needed;
3. server-option change pruning;
4. removed-remote pruning without disturbing an active sibling;
5. retention of a current unsupported-capability cached client;
6. Phase294 truncated-response rejection flowing through refresh eviction and fallback.

The full historical suite is required on Python 3.9 and Python 3.13 with the CI runner's native Git implementation.
