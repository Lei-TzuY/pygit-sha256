# Phase 290 — serialize promisor size refresh per repository

Phase 290 hardens the repository-scoped protocol-v2 `object-info size` reuse added in Phases 287–289 for concurrent callers.

## Problem

The cached `SmartHttpV2ObjectInfoClient` carries mutable capability-discovery state, and `refresh_promisor_sizes()` also reads and updates the promisor sidecar. Before this phase, two threads sharing the same in-process `Repository` could enter the refresh path at the same time, race cache creation, issue concurrent calls through the same cached client, or interleave sidecar state decisions.

The cache is intentionally process-local, so this is an in-process coordination problem rather than a Git wire-format problem.

## Design

Phase 290 adds two narrowly-scoped synchronization layers:

- a short-lived global `RLock` protects only weak-key cache bookkeeping and client/lock installation;
- one weak-keyed `RLock` per `Repository` serializes the actual refresh transaction for that repository.

The per-repository lock covers state inspection, bounded object-info queries, trusted-size persistence, failed-client eviction, and result collection. Network I/O never holds the global bookkeeping lock, so unrelated repositories remain able to refresh concurrently.

The client cache helpers also use the bookkeeping lock directly, which prevents duplicate `SmartHttpV2ObjectInfoClient` construction for one repository/effective remote key even if a future caller reaches those helpers outside `refresh_promisor_sizes()`.

Both the client cache and refresh-lock cache retain weak repository keys. The synchronization layer therefore remains process-local session state and does not extend a `Repository` object's lifetime.

## Git / SHA-256 compatibility

No Git protocol grammar, request batching, fallback order, server-option forwarding, or object identity semantics change.

- protocol-v2 `object-info size` requests still use genuine remote-native 40-hex SHA-1 OIDs;
- only scalar sizes are persisted;
- no content materialization is introduced;
- no SHA-1 padding/translation or surrogate local SHA-256 is introduced;
- the existing strict caller policy for unresolved size metadata is unchanged.

## Tests

`tests/test_phase290.py` covers:

- same-repository refresh blocking behind its repository lock;
- different repositories remaining concurrent;
- concurrent lookup constructing only one client for one effective cache key;
- weak-key lock-cache cleanup after the repository becomes unreachable.

Phase 290 is stacked on Phase 289 / PR #266 exact-green head `bd64b087f8cccfdc620f466ed356f8858f9c504b` and intentionally remains unmerged.
