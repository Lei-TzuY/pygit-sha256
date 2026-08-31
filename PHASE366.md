# Phase366: EINTR-safe durable lock directory fences

Phase366 hardens the shared durable-owned-lock release boundary introduced in Phase361/362 and integrated into production by Phase365.

## Problem

On POSIX, a successful transaction-owned lock removal is not considered durably complete until the containing directory has been `fsync()`ed. The Phase361 helper correctly performs that fence, but treated `InterruptedError` from `os.fsync()` as a hard durability failure.

A signal may interrupt `fsync(2)` before completion. In that case the correct low-level behavior is to retry the same descriptor rather than converting a transient EINTR into a permanent transaction failure.

This matters after the lock pathname has already been removed: reporting failure solely because the durability syscall was interrupted can make callers treat a completed namespace mutation as an unrecoverable storage error even though the same fence can be retried immediately.

## Change

`pygit.durable_owned_lock` now provides `_fsync_retry(fd)` and routes POSIX parent-directory durability fences through it.

The contract is deliberately narrow:

1. call `os.fsync(fd)`;
2. retry only `InterruptedError`;
3. return after the first successful fsync;
4. propagate every non-EINTR exception unchanged;
5. close the directory descriptor in the existing `finally` path.

Windows keeps the existing explicit boundary: Python does not expose the same directory-fd fsync guarantee there, so the helper remains a no-op rather than claiming POSIX power-loss semantics.

## Compatibility

Phase366 does **not** change the Phase362 batch observable contract. Multiple owned locks are still released in reverse acquisition order, and each successfully removed lock still gets its own parent-directory durability fence. The abandoned Phase363 sibling-fence coalescing behavior is intentionally not reintroduced.

The production paths installed by Phase365 therefore gain EINTR resilience without changing their lock names, ownership registries, release ordering, first-error behavior, or Path-shaped private seams.

## Tests

`tests/test_phase366.py` covers:

- repeated `InterruptedError` followed by successful fsync;
- non-EINTR failure propagation;
- directory descriptor closure after an interrupted/retried fence;
- successful owned-lock release after a directory-fsync interruption;
- Phase362 reverse-order batch semantics while the first directory fence is interrupted and retried.

The repository's full Python 3.9 and 3.13 GitHub Actions suite remains the authoritative regression gate.

## SHA-256-native invariants

No object identity or protocol behavior changes in Phase366.

- remote/native compatibility identities remain genuine full 40-hex SHA-1 values;
- local objects, refs, reflogs, `FETCH_HEAD`, and object-map identities remain genuine content-derived full 64-hex SHA-256 values;
- no padding, truncation, object-id text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.
