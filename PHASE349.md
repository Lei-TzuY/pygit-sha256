# Phase349: Harden publication-guard acquisition

Phase349 closes a fail-closed cleanup gap in the repository-wide metadata guards used by protocol-v2 packfile-URI transactions.

## Problem

The Phase326 guard set creates canonical `HEAD.lock`, `packed-refs.lock`, `promisor.json.lock`, and `shallow.lock` files before the final mutable-state revalidation and ref transaction.

Previously a guard path was appended to the caller's `acquired` list only after its marker had been written and fsynced. If `os.write()` or `os.fsync()` failed after `O_CREAT|O_EXCL` had already created the current path, outer rollback removed only earlier completed locks. The current half-initialized lock could remain behind and make later fetches fail with permanent-looking lock contention.

The descriptors also relied on Python's default close-on-exec behavior rather than making the non-inheritance boundary explicit like the newer `FETCH_HEAD` lock paths.

## Implementation

Phase349 splits one-lock setup into explicit helpers:

- `_open_publication_guard_lock()` uses `O_CREAT|O_EXCL`, requests `O_CLOEXEC` where available, and explicitly calls `os.set_inheritable(fd, False)`;
- descriptor-hardening failure closes the just-created fd and removes only the path created by that call;
- `_initialize_publication_guard_lock()` writes the guard marker, fsyncs it, closes it, and removes the current path on any setup failure;
- `_acquire_publication_guard_locks()` adds a path to `acquired` only after that full initialization succeeds;
- the existing outer rollback then remains responsible only for guards that were fully acquired earlier in the sequence.

A pre-existing foreign lock still fails closed and is never overwritten, stolen, or deleted.

## Why this matters for incremental fetch

Phases347-348 now hold these publication guards inside the correlated populated-`FETCH_HEAD` + tracking-ref publication window. A stranded guard from an injected I/O failure would therefore block later otherwise-safe incremental fetches even though no writer still owned it. Phase349 makes acquisition failure self-cleaning without weakening lock contention semantics.

## SHA-256-native invariants

This phase changes only local lock ownership and failure cleanup. Remote compatibility and negotiation identities remain genuine complete 40-hex SHA-1 values. Local objects, refs, and `FETCH_HEAD` remain genuine content-derived complete 64-hex SHA-256 values. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived object identity is introduced.

## Regression coverage

`tests/test_phase349.py` verifies:

- successful guard descriptors are explicitly non-inheritable;
- failure while fsyncing a later guard removes both the current half-initialized path and every previously acquired guard;
- descriptor-hardening failure removes the just-created path;
- an existing foreign lock is preserved byte-for-byte;
- successful acquisition still leaves the canonical marker files in place until explicit release.

## Coordination

- actual `main` rechecked at `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase348 / PR #325 head `3b26e60f8e8a08e3c99b7b2759de115c4ac8efb0`;
- Phase348 was already occupied by parallel work, so this run collision-checked and used Phase349;
- Phase349 remains stacked, open, and unmerged.
