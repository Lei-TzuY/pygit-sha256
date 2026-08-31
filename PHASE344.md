# Phase 344 — Durable FETCH_HEAD publication

Phase344 strengthens the mapped incremental protocol-v2 packfile-URI fetch path at its remaining mutable metadata boundary: `FETCH_HEAD` replacement.

## Ordering

The named-remote incremental path now uses:

`discover -> durable empty FETCH_HEAD replacement -> negotiate/download -> SHA-256 stage -> durable LMAP -> certify -> durable populated FETCH_HEAD replacement -> guarded CAS refs`

Both replace-style `FETCH_HEAD` writes use a same-directory temporary file. The complete replacement bytes are written and `fsync()`ed, then installed with `os.replace()`, followed by a directory `fsync` on POSIX before success is reported.

An empty ref mapping therefore truncates stale `FETCH_HEAD` through the same atomic/durable sequence rather than by opening the live file with `"w"`.

## Failure model

- A failure before `os.replace()` preserves the old `FETCH_HEAD` and removes the temporary file.
- A failure during the final directory durability fence may leave the new complete `FETCH_HEAD` visible. The exception is propagated; callers must not proceed as if the metadata were durable.
- The post-certification `FETCH_HEAD` update still intentionally precedes tracking-ref CAS publication, preserving native Git's useful behavior where a successfully fetched tip can remain recorded even if a later local ref update fails.
- The initial empty replacement remains after successful protocol-v2 discovery if a later negotiation/import step fails, matching Phase340's stale-file clearing semantics.
- Windows keeps atomic `os.replace()` publication but does not claim a POSIX directory-fd power-loss guarantee.

## SHA-256-native boundary

`FETCH_HEAD` stores repository-native object identifiers only. Phase344 validates that every rendered fetched object id is a full 64-hex SHA-256 value. Remote transport identities remain genuine full 40-hex SHA-1 values and never cross into this file. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived object identity is introduced.

## Scope

The historical generic `write_fetch_head(..., append=...)` API is left unchanged because append semantics need separate concurrency/locking policy. Phase344 adds a replace-only durable writer and binds the incremental named-remote path to it.

## Regression coverage

`tests/test_phase344.py` covers production binding, stale-file replacement, durable empty truncation, replace failure preserving the previous file and cleaning temporary state, post-replace directory-fsync failure propagation, full SHA-256 oid validation, and the explicit Windows directory-fsync boundary.
