# Phase 345 — Serialized durable FETCH_HEAD publication

Phase344 made replace-style `FETCH_HEAD` writes crash-safe. Phase345 adds the
missing concurrent-writer boundary and aligns that replacement path with Git's
lockfile discipline.

## Git compatibility

Git's lockfile API updates a file by creating `<filename>.lock` with
`O_CREAT|O_EXCL`, writing the complete replacement there, and atomically
renaming the lockfile to the final pathname. The exclusive create makes a
second writer fail instead of overwriting an in-progress update; readers see
only the old complete file or the new complete file.

The mapped incremental fetch path now uses exactly one canonical
`.pygit/FETCH_HEAD.lock` writer lock rather than an unrelated randomized
same-directory temporary pathname.

## Publication ordering

`render -> O_EXCL FETCH_HEAD.lock -> write -> fsync(lock) -> replace FETCH_HEAD -> fsync(.pygit)`

Rendering and SHA-256 validation occur before lock acquisition. Once the lock
is owned, any write/fsync/replace failure rolls back only that lock. A lock
that already existed before the call is never overwritten, removed, or treated
as stale.

If the final directory fsync fails after the atomic rename, the new complete
`FETCH_HEAD` may already be visible. The error is propagated and the enclosing
incremental transaction must not advance tracking refs, preserving Phase344's
durability contract.

## Concurrency model

Two concurrent replace-style writers cannot both enter the publication
critical section. One acquires `FETCH_HEAD.lock`; the other receives
`FileExistsError` and leaves both the live `FETCH_HEAD` and the first writer's
lock untouched.

This phase intentionally does not add waiting, stale-lock reclamation, or
append-mode composition. Those policies require process-liveness and command
semantics beyond the replace-only incremental-fetch path.

## SHA-256-native invariants

`FETCH_HEAD` continues to contain only full 64-hex repository-native SHA-256
object ids. Remote negotiation continues to use genuine full 40-hex SHA-1
identities. The lock protocol changes publication ownership only; it does not
pad, truncate, re-hash identifier text, synthesize surrogate SHA-256 ids, or
derive object identity from metadata.

## Tests

`tests/test_phase345.py` covers foreign-lock contention without stealing,
canonical lockfile commit, owned-lock cleanup on fsync/replace failure,
validation-before-lock ordering, and the post-rename directory-fsync failure
boundary. The inherited full suite remains the authoritative compatibility
gate.
