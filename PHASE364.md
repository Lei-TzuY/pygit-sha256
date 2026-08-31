# Phase364 — integrate shared durable owned-lock release

Phase361–363 introduced a reusable descriptor-backed lock cleanup contract but left the three production registries on their historical local release loops. Phase364 connects the shared primitive to all three packfile-URI ownership layers while preserving their established Path-shaped caller seams.

## Production integration

At package initialization, `install_durable_owned_lock_release_integration()` binds:

- repository-wide publication guards to `release_owned_locks_durably()`;
- `FETCH_HEAD.state.lock` to `release_owned_lock_durably()`;
- canonical target-ref locks to `release_owned_locks_durably()`.

Each registry still owns its native Phase353/356/360 dataclass during acquisition. At release, the retained `fd`, `st_dev`, and `st_ino` are converted to the shared `OwnedLockIdentity`. Registry entries are popped before cleanup, so failed durability fences cannot leave stale in-process ownership state.

The shared primitive preserves replacement and missing pathnames, closes every retained descriptor, removes only a pathname that still names the acquired inode, and on POSIX requires the containing directory namespace to cross an fsync fence before cleanup reports success. Batch callers release in reverse acquisition order and coalesce sibling directory fences.

## Failure model

If unlink succeeds but the parent-directory fsync fails, the lock may already be absent from the live namespace, but the exception propagates. The caller therefore cannot claim durable transaction cleanup success. Remaining batch-owned descriptors and sibling locks are still processed according to Phase362/363 first-error semantics.

A pathname that was removed and recreated by another actor is never unlinked by the stale owner, even when replacement bytes match the historical marker. Windows retains the established atomic/inode-aware behavior without claiming a POSIX-equivalent directory-fsync power-loss guarantee.

## Compatibility

No Git-visible ref, lock marker, protocol, refspec, FETCH_HEAD, object-map, or object format changes. Acquisition behavior remains `O_CREAT|O_EXCL` with retained non-inheritable descriptors. Existing callers still pass `Path` values to the same private release seams.

## SHA-256-native invariants

Remote/native compatibility identity remains genuine full 40-hex SHA-1. Local objects, refs, reflogs, FETCH_HEAD, and object-map identities remain genuine content-derived full 64-hex SHA-256. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.
