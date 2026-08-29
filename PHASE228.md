# Phase 228 — Promisor-aware status batching

Phase228 removes another partial-clone demand-fetch waterfall from `Repository.status()` while keeping status semantics and pygit's SHA-256-native repository model unchanged.

## Problem

`Repository.status()` begins by flattening the complete HEAD tree so it can compare HEAD with the persistent index. A filtered foreign tree may still contain unresolved native Git SHA-1 blob identities. Without prefetching, flattening that snapshot can fault promised blobs in one at a time.

This is especially visible after a filtered no-checkout clone, where HEAD exists but the working tree/index may not yet have forced those blobs to materialize.

## Change

Phase228 wraps `Repository.status()` through the existing promisor installer chain:

1. read `.pygit/promisor.json`;
2. if no promises remain, call the historical status implementation immediately;
3. resolve HEAD without touching tree entries;
4. when HEAD exists, pass that single snapshot to the established history promisor prefetcher;
5. let the existing `Repository.status()` implementation perform all HEAD/index/worktree comparisons and formatting.

The prefetcher reuses the existing checkout-promise collector and multi-promisor materializer, so multiple unresolved blobs are deduplicated and fetched as one bulk demand. Single-object materialization keeps the Phase213 compatibility seam, while multi-object requests retain Phase221/222 fallback ordering, primary-promisor-last behavior, batch shrinking, and per-remote `serverOption` forwarding.

## Compatibility

- ordinary repositories remain network-free;
- repositories whose promisor sidecar has no unresolved promises remain network-free;
- empty repositories with no HEAD do not attempt a promisor fetch;
- the `ignored=True` status option is forwarded unchanged;
- staged, unstaged, untracked, ignored, conflict, branch, and upstream calculations remain owned by the historical status implementation;
- no protocol, pack, tree serialization, index, ref, or worktree mutation logic changes.

## SHA-256-native boundary

Native Git SHA-1 remains confined to foreign-tree/promisor metadata and protocol requests. Materialized blob contents are stored under their real content-derived SHA-256 IDs before the historical status code consumes `TreeEntry.sha`. No surrogate SHA-256 identities are introduced.

## Tests

`tests/test_phase228.py` covers:

- one HEAD-snapshot prefetch plan when promises exist;
- preservation of the `ignored=True` option after prefetch;
- ordinary repositories staying on the network-free historical path;
- empty repositories with promisor metadata avoiding network activity.
