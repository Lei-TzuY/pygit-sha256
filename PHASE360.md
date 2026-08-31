# Phase360: Own canonical target-ref locks through durability

Phase360 closes the last path-only lock ownership boundary in the Phase359 incremental packfile-URI publication stack.

## Problem

Phase359 integrates three strong outer guarantees:

- fork-safe `FETCH_HEAD.state.lock` ownership;
- fork-safe repository-wide metadata guard ownership;
- durable tracking-ref / reflog publication.

However, the inner canonical target locks (`<ref>.lock`) still used the older Phase323 acquisition model:

- one unchecked `os.write()` marker call;
- setup descriptor closed immediately after fsync;
- cleanup performed by pathname-only `unlink()`.

That leaves two correctness gaps.

First, a positive short write or interrupted write could be treated as a complete initialization marker. Second, an external unlink/recreate of `<ref>.lock` while the transaction is active can cause pathname-only cleanup to delete a replacement lock owned by another writer.

The same locks also need a fork boundary once descriptors are retained: `FD_CLOEXEC` protects exec, not a plain POSIX fork.

## Implementation

`pygit.protocol_v2_packfile_uri_refs` now gives target locks an explicit process-local ownership record:

`_RefLockOwnership(fd, device, inode)`

Acquisition is:

`O_EXCL create -> non-inheritable fd -> complete marker write -> fsync -> fstat identity -> retain fd -> register ownership`

Marker writes use a remaining-buffer loop:

- retry `InterruptedError`;
- continue after positive short writes;
- reject zero/negative progress;
- never report initialization complete before every marker byte is consumed.

Release pops the ownership record, compares the live pathname's non-following `(st_dev, st_ino)` with the retained descriptor identity, and unlinks only on an exact match. Missing or replaced pathnames are preserved. The retained descriptor is then closed.

The public `_acquire_locks()` compatibility seam still returns `list[Path]`, so existing Phase323/350 callers and tests do not need a new API.

## Durability ordering

Phase350's success-after-durability contract is unchanged. The target ownership descriptor is retained through ref/reflog file fsync, then the transaction-owned target pathname is released before directory durability fences run.

Normal ordering is therefore:

`target O_EXCL -> complete marker -> target fsync -> retained target ownership -> ref CAS -> ref/reflog fsync -> owner-aware target lock release -> directory fsync`

A replacement target lock is deliberately not removed by this transaction.

## Fork safety

Phase358's generic child cleanup now includes:

`pygit.protocol_v2_packfile_uri_refs._REF_LOCK_OWNERSHIP`

After a POSIX fork, the child closes inherited target-ref ownership descriptors and clears only its copied registry. It does not unlink target lock pathnames. The parent retains its original descriptor and registry entry and remains the only process able to release through normal inode-aware cleanup.

## Regression coverage

`tests/test_phase360.py` verifies:

- forced short writes produce the complete target lock marker and retain a non-inheritable ownership fd;
- zero-progress writes fail closed without a residual lock or registry entry;
- unlink/recreate replacement target locks survive owner release while the original ownership fd closes;
- a real fork child receives an empty target-lock ownership registry, a closed inherited ownership fd, cannot release the parent's lock, and cannot reacquire the canonical pathname;
- ref/reflog durability fsync runs while the retained target-lock ownership fd remains valid.

Inherited Phase350 tests continue to verify file-vs-directory durability ordering and success-after-durability failure semantics. Phase358/359 continue to cover the two outer ownership layers.

## SHA-256-native invariants

This phase changes only local lock ownership bookkeeping:

- remote/native compatibility identities remain genuine full 40-hex SHA-1 values;
- local objects, refs, reflogs, `FETCH_HEAD`, and LMAP identities remain genuine content-derived full 64-hex SHA-256 values;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase359 / PR #336 head `d193f1c2ad1d61432f7a94a69d5a01612361626b`;
- Phase359 Tests #3044: Python 3.9 / 3.13 both **2644 passed**, Git 2.55.0;
- Phase360 was collision-checked immediately before branch creation and was free.

This phase remains stacked, open, and unmerged until its own exact-head CI is green.
