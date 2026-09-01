# Phase369 — EINTR-safe FETCH_HEAD state-lock acquisition

Phase369 completes the shared acquisition-side durability contract for the retained `FETCH_HEAD.state.lock` used by incremental protocol-v2 packfile-URI fetch publication.

## Motivation

Phase354 made the state-lock marker robust against short writes and explicit write interruption. Phase356 then retained a duplicated non-inheritable descriptor so release can prove `(st_dev, st_ino)` ownership before unlinking the canonical pathname. Phase366 introduced the shared `_fsync_retry(fd)` primitive, and Phase368 applied it to repository publication guards and target-ref locks.

The remaining state-lock acquisition path still called `os.fsync(fd)` directly. A transient `InterruptedError` at that one durability fence could therefore abort an otherwise fully written lock before the retained ownership descriptor was established.

## Implementation

`durable_owned_lock_integration.install_durable_owned_lock_release_integration()` now installs an EINTR-safe `FETCH_HEAD.state.lock` acquisition wrapper in addition to the Phase368 publication/ref lock wrappers.

The state machine remains:

1. reject an already-retained or already-existing state lock;
2. create the canonical lock with `O_CREAT|O_EXCL`;
3. make the setup descriptor non-inheritable;
4. write the complete Phase354 marker;
5. durability-fence the marker through shared `_fsync_retry(fd)`;
6. duplicate the descriptor;
7. make the retained duplicate non-inheritable;
8. derive retained `(st_dev, st_ino)` identity from that duplicate;
9. close the setup descriptor;
10. register retained ownership only after all prior setup succeeds.

Only `InterruptedError` from the fsync boundary is retried. Every other failure keeps the existing fail-closed cleanup contract: no ownership registry entry is published, every descriptor still owned by the failed acquisition is closed, and only the pathname created by that acquisition is removed.

The setup-descriptor close path is intentionally not given an application-level EINTR retry loop. Python's PEP 475 treats `os.close()` specially because retrying a close after EINTR may target an fd number that has already been closed and reused by another thread. Phase369 therefore changes the fsync boundary, not Python's close semantics.

## Regression coverage

`tests/test_phase369.py` verifies:

- one interrupted state-lock fsync is retried on the same descriptor;
- repeated EINTR is retried until the durability fence succeeds;
- retained inode identity and non-inheritable ownership survive the retry;
- non-EINTR fsync failure propagates and removes the transaction-owned path;
- a failure while duplicating the ownership descriptor closes the setup fd and leaves no registry state;
- retained-descriptor hardening failure closes both descriptors and leaves no registry state.

Inherited Phase354/356/365 tests continue to cover marker short writes, descriptor-hardening/close failures, replacement-path preservation, descriptor-backed ownership, durable release, and cross-process cleanup.

## SHA-256-native invariants

This phase changes only transient filesystem durability handling. Remote/native compatibility identities remain genuine full 40-hex SHA-1 values where Git interoperability requires them. Local objects, refs, reflogs, `FETCH_HEAD`, and LMAP identities remain genuine content-derived full 64-hex SHA-256 values. No padding, truncation, object-id text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.

## Coordination

- exact base: Phase368 / PR #345 head `c3ebffa91803f460c2a79bf3303b524e9631df47`;
- Phase368 Tests #3099 / run `33455675835`: success;
- Python 3.9 / 3.13 on that base: 2678 passed each;
- CI Git on that base: 2.55.0;
- Phase369, Phase370, and Phase371 namespaces were collision-checked before branch creation and were free;
- this phase intentionally remains stacked, open, and unmerged.
