# Phase346: multi-process FETCH_HEAD contention hardening

Phase345 moved durable replace-style `FETCH_HEAD` publication onto Git's canonical
`FETCH_HEAD.lock` / `O_CREAT|O_EXCL` lockfile discipline. Phase346 validates that
boundary across independent operating-system processes and makes the lock file
descriptor explicitly non-inheritable.

## Production change

`_acquire_fetch_head_lock()` now requests `O_CLOEXEC` where the platform exposes
it and always calls `os.set_inheritable(fd, False)` before returning the owned
lock descriptor. If that descriptor hardening itself fails, the descriptor is
closed and only the lock created by that call is removed before the error is
propagated.

This is deliberately a descriptor-lifetime hardening, not a stale-lock policy.
A pre-existing `FETCH_HEAD.lock` is still never stolen, truncated, unlinked, or
classified as stale.

## Multi-process semantics

The Phase346 regressions use the multiprocessing `spawn` context so the tests do
not depend on inherited Python state.

1. One process acquires and holds the canonical lock while another process runs
   the real durable writer. The second writer must receive `FileExistsError`,
   must not change the live `FETCH_HEAD`, and must leave the first process's lock
   bytes untouched.
2. Eight independent writers are released as one burst. Each result may be a
   successful complete replacement or an explicit lock contention. A writer
   that reaches `O_EXCL` after an earlier writer has already renamed its lock may
   validly serialize and succeed; Git-style lockfiles serialize the critical
   section rather than creating a permanent single-winner election.
3. After the burst, `FETCH_HEAD` must byte-for-byte equal one complete payload
   from a successful writer. Interleaved/torn output and leftover
   `FETCH_HEAD.lock` files are forbidden.

This distinction is important: claiming that exactly one concurrent writer must
succeed would be stronger than Git's lockfile primitive actually guarantees.
The guaranteed property is mutually exclusive publication while the lock exists,
combined with atomic replacement of complete files.

## SHA-256-native boundary

Nothing about object identity changes in this phase.

- local `FETCH_HEAD` entries remain full 64-hex repository-native SHA-256 ids;
- remote protocol negotiation continues to use genuine full 40-hex SHA-1 ids;
- no padding, truncation, object-id text rehashing, surrogate SHA-256, or
  metadata-derived identity is introduced.

## Transaction ordering

The inherited mapped incremental fetch ordering remains:

`discover -> durable empty FETCH_HEAD -> negotiate/download -> SHA-256 stage -> durable LMAP -> certify -> durable populated FETCH_HEAD -> guarded CAS refs`

Phase346 only strengthens the process ownership of the two durable `FETCH_HEAD`
replacement boundaries.
