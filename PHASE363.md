# Phase363: Coalesced durable owned-lock fences

Phase361 introduced a reusable inode-aware single-lock release primitive and Phase362 extended it to best-effort multi-lock cleanup. Phase363 tightens the batch durability boundary for the common case where several transaction-owned locks share a parent directory.

## Motivation

Repository publication guards such as `HEAD.lock`, `packed-refs.lock`, `promisor.json.lock`, and `shallow.lock` all live directly under `.pygit`. Target-ref transactions likewise frequently remove several sibling lockfiles from the same directory. Phase362 called the single-lock durable release primitive once per lock, which meant a parent-directory `fsync()` after every sibling unlink.

Those repeated fences are correct but unnecessary. One directory `fsync()` after all owned sibling namespace removals is sufficient to durably fence the complete set of changes for that directory.

## Batch ordering

`release_owned_locks_durably()` now performs two explicit stages:

1. Walk locks in reverse acquisition order, verify live `(st_dev, st_ino)` ownership, unlink only pathnames that still name the retained inode, and close every retained ownership descriptor.
2. Group successful namespace removals by parent directory and `fsync()` each affected parent at most once, in first-mutation order.

The single-lock `release_owned_lock_durably()` contract is unchanged and still performs its own immediate parent-directory fence.

## Failure model

Cleanup remains best-effort across the complete group. The first unlink or directory-fsync exception is retained, later lock releases and later directory fences still run, and the first exception is re-raised only after cleanup is exhausted.

If one parent-directory fence fails, pathnames in other changed directories are still fenced. A failed directory fence means callers must not infer durable success for removals in that directory even though those pathnames may already be absent from the live namespace.

Missing or replaced pathnames are never unlinked and do not cause an unnecessary directory fence. Replacement detection remains inode-based rather than marker-content-based.

## Platform boundary

On POSIX, changed parent directories are opened and fsynced. On Windows, Python does not expose the same directory-fd durability contract, so the existing explicit no-op boundary remains: inode-aware/atomic behavior is preserved without claiming POSIX-equivalent power-loss durability.

## SHA-256-native invariants

This phase changes only lock cleanup mechanics. Remote/native compatibility identities remain genuine full 40-hex SHA-1 values. Local objects, refs, reflogs, `FETCH_HEAD`, and object-map identities remain genuine content-derived full 64-hex SHA-256 values. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.

## Tests

`tests/test_phase363.py` covers:

- one directory fence for several sibling lock removals;
- one fence per changed parent across nested ref/reflog lock groups;
- continued fencing of later directories after an earlier directory-fsync failure;
- preservation of replacement pathnames without spurious fences;
- failure propagation when the only parent-directory fence fails;
- retained descriptor closure across success and failure paths.

The full existing pytest suite remains the authoritative regression gate on GitHub Actions for Python 3.9 and 3.13.
