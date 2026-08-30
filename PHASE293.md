# Phase 293 — prune stale promisor object-info clients

Phase 293 bounds the lifetime of repository-scoped protocol-v2 `object-info size` clients when a long-lived `Repository` changes remote configuration.

## Problem

Phase 288 correctly keys cached clients by `(remote name, URL, server options)`, so a configuration change can never reuse capability state from a different effective remote. The old key, however, previously remained in the weak-keyed repository cache until the whole `Repository` object was garbage-collected.

A process that repeatedly rewrites remote URLs or `remote.<name>.serverOption` values could therefore retain every historical smart-HTTP client for the lifetime of one Repository instance. Removed promisor remotes had the same lifetime issue.

## Design

During each non-empty `refresh_promisor_sizes()` invocation, Phase 293 now computes the complete current set of effective promisor configurations:

`(remote name, current URL, ordered current server options)`

While Phase 292's per-repository refresh lock is held, cached clients whose keys are not in that set are pruned before any new object-info query begins.

This has three useful properties:

- changing a remote URL removes the old session before a replacement is created;
- changing configured server options removes the old effective-configuration session;
- removing a promisor remote removes its cached session while keeping still-active sibling remotes untouched.

Pruning also occurs when all requested sizes are already persisted, as long as the caller made a non-empty refresh request. This lets configuration cleanup happen without forcing a network query or creating a replacement client.

The cache helper still takes the short global bookkeeping guard, while network I/O remains protected only by the per-repository lock. Different repositories therefore remain concurrent.

## Phase 291 / 292 semantics retained

A client whose current effective configuration explicitly lacks `object-info` remains cached. Phase 293 only removes clients whose effective configuration is no longer current.

Real transport/protocol/query failures still use Phase 289 identity-guarded eviction. Same-repository refresh serialization and weak-key lifetimes remain unchanged from Phase 292.

## Git / SHA-256 compatibility

No Git wire grammar or object identity semantics change.

- protocol-v2 requests still use genuine remote-native 40-hex SHA-1 OIDs;
- deterministic remote ordering, bounded request batches, server-option ordering, and fallback behavior are unchanged;
- only scalar object sizes are persisted;
- no content materialization is introduced;
- no SHA-1 padding/translation, surrogate SHA-256, or native-to-local mapping is introduced.

## Tests

`tests/test_phase293.py` covers:

- remote URL changes pruning the old client before replacement;
- stale configuration cleanup even when no network query is required;
- server-option changes pruning the old effective key;
- removed promisor remotes being pruned without evicting an active sibling client;
- current unsupported-capability clients surviving normal pruning.

Phase 293 is stacked on Phase 292 / PR #269 exact-green head `4602f5341ce63de2afe170ae08c115ea172adb13` and intentionally remains unmerged.
