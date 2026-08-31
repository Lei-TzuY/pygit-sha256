# Phase354: Harden FETCH_HEAD state-guard initialization

Phase354 hardens Phase348's short-lived `FETCH_HEAD.state.lock` setup so the lock is not treated as initialized until its marker is completely written, fsynced, and the owned descriptor closes successfully.

## Problem

Phase348 introduced `FETCH_HEAD.state.lock` to serialize the early stale `FETCH_HEAD` clear with the final populated `FETCH_HEAD` + tracking-ref publication window. Its mutual exclusion was correct, but setup still used a single unchecked `os.write(fd, marker)` and an unconditional `os.close(fd)` in `finally`.

Low-level `write()` may make positive short progress or be interrupted. A zero-progress write must not be retried forever. In addition, if the final close fails, the transaction must not leave a lock that callers may mistake for a fully initialized live owner.

## Implementation

`pygit.protocol_v2_packfile_uri_incremental_fetch` now defines one canonical `_FETCH_HEAD_STATE_GUARD_MARKER` and writes it through `_write_fetch_head_state_guard_marker(fd)`.

The helper:

- retries `InterruptedError` without consuming marker bytes;
- resumes after positive short writes from the exact remaining suffix;
- rejects zero or negative progress with `OSError`;
- returns only after the complete marker has been consumed.

`_acquire_fetch_head_state_guard()` now owns the descriptor/path until all setup steps complete:

`O_EXCL create -> non-inheritable fd -> complete marker -> fsync -> close -> committed`

If descriptor hardening, marker writing, fsync, or close fails, the helper closes the descriptor where possible and removes only the lock path created by the current transaction. A foreign pre-existing lock is still never stolen or removed.

## Concurrency contract

This phase does not change the lifetime of the state guard. It remains short-lived:

- early clear: held only across the durable empty `FETCH_HEAD` replacement;
- final publication: held across repository publication guards, state revalidation, populated `FETCH_HEAD`, and tracking-ref CAS;
- network, pack download, SHA-256 staging, durable LMAP publication, and root certification remain outside the state lock.

The existing canonical `FETCH_HEAD.lock` continues to own each individual durable file replacement.

## Tests

`tests/test_phase354.py` covers:

- short marker writes complete before `fsync()`;
- EINTR retry;
- zero-progress fail-closed behavior with no fsync;
- descriptor-hardening failure cleanup;
- close-failure cleanup;
- repeated tiny writes producing the exact marker;
- defensive negative-progress rejection.

Inherited Phase348 tests continue to exercise real state-lock ownership and cross-process contention.

## SHA-256-native invariants

Phase354 changes only local lock initialization. Remote/native compatibility identities remain genuine complete 40-hex SHA-1 values; local objects, refs, and `FETCH_HEAD` remain genuine complete content-derived 64-hex SHA-256 values. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base is Phase351 / PR #328 head `e256045637ad9a19e7bceebaa9d61fb6be0c298c`;
- Phase351 Tests #2972: Python 3.9 / 3.13 both 2610 passed, Git 2.55.0;
- Phase352 and Phase353 were already occupied by parallel work when this phase was created;
- Phase350 remains a sibling ref-durability line and is not duplicated here.

This phase intentionally remains an open, unmerged stacked pull request.
