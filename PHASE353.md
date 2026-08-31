# Phase353: publication guard release ownership

Phase353 hardens the repository-wide protocol-v2 packfile-URI publication guards at the cleanup boundary.

## Problem

Phase349 made guard creation fail closed and Phase351 made marker writes complete across short writes and `EINTR`. The remaining release path still accepted only a pathname and unconditionally unlinked it.

That is safe for cooperative writers that never disturb another process's lockfile, but it has an ownership hole if a guard pathname is removed and recreated while the original transaction remains active. The original transaction would later unlink the replacement even though it no longer owns that filesystem object.

## Implementation

The normal transaction acquisition path now keeps each initialized guard descriptor open until release.

For every acquired guard pygit records:

- the non-inheritable open descriptor;
- `st_dev`;
- `st_ino`.

The descriptor is obtained from the same `O_CREAT|O_EXCL` lock creation boundary already used by Phase349. The complete canonical marker is written with Phase351's full-write loop and fsynced before the descriptor identity is accepted.

Release performs a non-following stat of the current pathname and unlinks only when its `(st_dev, st_ino)` still matches the retained descriptor. Missing or replaced paths are left untouched. The retained descriptor is then closed exactly once regardless of whether unlink happened.

Keeping the descriptor open also prevents the original inode from being recycled while the transaction still owns it, which makes the identity comparison materially stronger than recording an inode number and closing the descriptor immediately.

The standalone `_initialize_publication_guard_lock()` helper remains compatible with Phase349/351 tests and callers: it still closes the descriptor on successful initialization. The transaction acquisition path uses the new retaining boundary.

## Failure and concurrency model

- a foreign pre-existing canonical lock still fails closed and is never stolen or removed;
- initialization failure cleans the just-created lock and descriptor;
- failure after earlier guards were acquired rolls them back and closes their retained descriptors;
- if an owned pathname disappears before release, cleanup closes the original descriptor and leaves the missing path alone;
- if an owned pathname is recreated, cleanup preserves the replacement even when its bytes are identical to pygit's canonical guard marker;
- release of a path without a recorded ownership handle is a no-op.

This does not attempt to make hostile arbitrary pathname replacement fully race-free; native Git lockfiles also rely on cooperative ownership. The improvement closes pygit's previous deterministic path-only cleanup error and prevents stale transaction cleanup from intentionally deleting a replacement it can already identify as foreign.

## Git compatibility

The visible lock names and lock acquisition model are unchanged: canonical `*.lock` paths are still created with `O_CREAT|O_EXCL`, written completely, fsynced, and treated as owned only by the creator. Phase353 changes only pygit's internal ownership bookkeeping and release discipline; it does not introduce a new Git-visible lockfile format.

## SHA-256-native invariants

Phase353 changes only local metadata-lock lifetime and cleanup.

- remote/native compatibility identities remain genuine full 40-hex SHA-1;
- local objects, refs, reflogs, and `FETCH_HEAD` remain genuine content-derived full 64-hex SHA-256;
- LMAP remains validated compatibility metadata;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local object identity is introduced.

## Tests

`tests/test_phase353.py` covers retained non-inheritable descriptors, descriptor/path identity equality, replacement preservation with arbitrary bytes, replacement preservation with the canonical marker, missing-path cleanup, unowned-path no-op behavior, rollback descriptor closure, and ownership-record cleanup.

Inherited Phase349 and Phase351 tests continue to cover exclusive creation, descriptor hardening, foreign-lock preservation, fsync failure rollback, short writes, `EINTR`, zero-progress writes, canonical marker bytes, and normal release.
