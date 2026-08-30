# Phase 292 — serialize promisor refresh atop cached capability absence

Phase 292 is the clean integration of Phase 290's concurrency hardening with Phase 291's explicit unsupported-`object-info` capability cache.

Phase 290 was developed as a sibling of Phase 291 from the same Phase 289 base. Rather than force-update or overwrite either validated branch, Phase 292 rebuilds the synchronization change on the exact-green Phase 291 head.

## Concurrency model

Two synchronization scopes are used:

- a short-lived global `RLock` protects weak-key cache bookkeeping and singleton client/lock installation;
- one weak-keyed `RLock` per `Repository` serializes the actual refresh transaction for that repository.

The per-repository lock covers promisor-state inspection, bounded `object-info size` queries, trusted-size persistence, failed-client handling, and result collection. Network I/O does not hold the global bookkeeping lock, so different repository instances can refresh concurrently.

Client cache helpers also use the bookkeeping guard directly, preventing duplicate smart-HTTP client construction for one `(repository, remote, URL, server-options)` key.

## Phase 291 semantics retained

`ObjectInfoUnsupportedError` remains distinct from a transport/protocol/query failure:

- an explicit successfully-discovered lack of `object-info` retains the cached client and its stable negative capability result;
- `OSError`, other `RuntimeError`, and `ValueError` failures still evict the exact failed client as established in Phase 289.

Serialization changes only in-process coordination. It does not broaden retries or alter fallback order.

## Git / SHA-256 compatibility

No Git wire grammar or object identity semantics change.

- requests remain protocol-v2 `object-info size` using genuine remote-native 40-hex SHA-1 OIDs;
- deterministic remote order, bounded batches, and server-option forwarding are unchanged;
- only scalar size metadata is persisted;
- no content materialization is introduced;
- no SHA-1 padding/translation, surrogate SHA-256, or native-to-local mapping is introduced.

## Tests

`tests/test_phase292.py` covers:

- same-repository serialization;
- different-repository concurrency;
- singleton concurrent client construction per effective cache key;
- retention of an unsupported-capability client across serialized refreshes;
- weak-key refresh-lock cleanup after a repository becomes unreachable.

Phase 292 is stacked on Phase 291 / PR #268 exact-green head `11e84b7b1424ced7205a499950ea55dca7f167d0` and intentionally remains unmerged.
