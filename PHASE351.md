# Phase351: Harden publication guard marker writes

Phase351 hardens Phase349's repository-wide packfile-URI publication guard initialization against short and interrupted low-level writes.

## Problem

Phase349 correctly made each guard path transaction-owned from `O_CREAT|O_EXCL` through descriptor hardening, marker write, `fsync()`, and close. However, the marker write still used one unchecked `os.write(fd, marker)` call.

POSIX `write()` does not promise that one successful call consumes the complete buffer. It may return a positive count smaller than the requested length, and an interrupted call may raise `InterruptedError`. Treating either case as a fully initialized guard would let the code `fsync()` and accept a partial marker even though the lock file was not completely initialized according to its own protocol.

The lock's existence is the mutual-exclusion primitive, so a partial marker does not by itself let another live writer enter. It does, however, weaken the explicit initialization contract and makes fault diagnosis ambiguous. More importantly, the code should not report a durability boundary until all bytes intended for that boundary have actually reached the file descriptor.

## Implementation

`pygit.protocol_v2_packfile_uri_transaction` now defines one canonical `_PUBLICATION_GUARD_MARKER` and writes it through `_write_publication_guard_marker(fd)`.

The helper:

- keeps a `memoryview` over the remaining marker bytes;
- retries `InterruptedError` without advancing the buffer;
- accepts positive short writes and continues from the exact remaining suffix;
- rejects zero or negative progress with `OSError`;
- returns only after every marker byte has been consumed.

`_initialize_publication_guard_lock()` then performs `fsync()` only after the helper returns. Any failure before that point reuses Phase349's existing transaction-owned cleanup: the current descriptor is closed and the just-created lock path is removed before control returns to the outer acquired-set rollback.

No retry is performed after a zero-progress write because that condition provides no forward-progress guarantee and could otherwise spin forever under a broken or fault-injected filesystem boundary.

## Ordering

One guard initialization is now:

`O_EXCL create -> non-inheritable fd -> complete marker write (EINTR retry / short-write loop) -> fsync -> close -> join acquired set`

A path never joins the acquired set before all of those steps succeed.

## Regression coverage

`tests/test_phase351.py` verifies:

- a forced first-call short write is completed before `fsync()` is allowed to run;
- one injected `InterruptedError` is retried and the complete marker is persisted;
- a zero-progress write fails before `fsync()` and removes the transaction-owned lock;
- the complete four-lock publication guard set survives repeated tiny writes and every live marker is complete;
- negative progress is rejected defensively by the marker helper.

Inherited Phase349 tests continue to cover non-inheritable descriptors, descriptor-hardening cleanup, fsync-failure cleanup of current and prior guards, foreign-lock preservation, and successful release.

## SHA-256-native invariants

Phase351 changes only local lockfile initialization. It does not alter object identity, transport negotiation, object-map compatibility, or ref contents:

- remote/native compatibility identities remain genuine complete 40-hex SHA-1 values;
- local objects and refs remain genuine content-derived complete 64-hex SHA-256 values;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase349 / PR #326 head `d630286b8a6ccb601360760579ae1877525614e2`;
- Phase349 GitHub Actions Tests #2960 completed successfully before this branch was created;
- CI runner Git on that base: 2.55.0;
- Phase350 is a sibling durability line based on Phase347 and is intentionally not duplicated here;
- `phase351` was collision-checked immediately before branch creation and was free.

This phase intentionally remains a stacked, open, unmerged pull request.
