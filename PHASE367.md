# Phase 367 — EINTR-safe FETCH_HEAD durability fences

Phase367 extends the shared durability semantics introduced for owned-lock release to the SHA-256-native `FETCH_HEAD` publication path.

## Motivation

`write_fetch_head_durable()` already follows the correct Git-style publication order:

1. acquire `FETCH_HEAD.lock` with exclusive creation,
2. write the complete replacement,
3. flush userspace buffers,
4. `fsync()` the lockfile,
5. atomically replace `FETCH_HEAD`,
6. `fsync()` the metadata directory on POSIX.

Before this phase, either `fsync()` call treated `InterruptedError` as a hard durability failure. POSIX signals may interrupt `fsync(2)` before completion, and retrying the same operation is the appropriate transient-error behavior. Phase366 already established a shared `_fsync_retry()` helper for durable lock cleanup; Phase367 reuses that exact primitive here rather than creating a second retry implementation.

## Contract

Both durability fences now retry only `InterruptedError`:

- lockfile `fsync` retries on the same descriptor before rename;
- parent-directory `fsync` retries on the same directory descriptor after rename;
- every non-EINTR `OSError` still propagates unchanged;
- a pre-rename failure removes only this transaction's `FETCH_HEAD.lock` and exposes no new `FETCH_HEAD`;
- a post-rename directory-fsync failure may leave the complete replacement visible, but the caller does not receive durable success.

Windows keeps the existing boundary: the file fence is used, while no POSIX-style directory-fd durability guarantee is claimed.

## Git compatibility and SHA-256-native invariants

No file format, lock name, ordering rule, or object identity changes in this phase. `FETCH_HEAD` continues to contain genuine full 64-hex SHA-256 object IDs. Remote/native compatibility identities remain genuine full 40-hex SHA-1 values where required by the existing interoperability layer. There is no padding, truncation, object-ID text rehashing, surrogate SHA-256, or metadata-derived local identity.

## Regression coverage

`tests/test_phase367.py` verifies:

- interrupted lockfile fsync is retried and publication completes;
- interrupted POSIX directory fsync is retried after replacement;
- non-EINTR lockfile-fsync failure rolls back the owned lock and publishes nothing;
- non-EINTR directory-fsync failure propagates after atomic replacement without leaving a lockfile.
