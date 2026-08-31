# Phase 343 — durable LMAP gating for incremental fetch

Phase342 introduced a success-after-durability boundary for Git-compatible LMAP v1 generations. Phase343 moves the mapped incremental packfile-URI transaction onto that stronger boundary.

## Transaction ordering

When new native objects are staged, the repository-side ordering is now:

`download -> stage -> durable immutable LMAP -> certify roots -> FETCH_HEAD hook -> guarded CAS refs`

The LMAP step uses `publish_staged_loose_object_map_durable()`, which preserves Phase341's content authentication, conflict preflight, writer locking, temp-file fsync and atomic immutable publication, then requires the Phase342 directory durability fences before returning success.

The historical module-level name `publish_staged_loose_object_map` remains as a compatibility/monkeypatch seam, but is bound to the Phase342 durable implementation. This keeps existing transaction tests stable while strengthening production behavior.

## Failure semantics

If LMAP publication or either durability fence fails, the exception propagates before root certification, before the named-remote `FETCH_HEAD` hook, before publication guard locks, and before tracking-ref CAS.

A map may already be atomically visible when a directory fsync fails. That is safe immutable compatibility metadata for objects already staged in the local SHA-256 store; Phase342 deliberately retains it for idempotent retry. Mutable repository history does not advance.

If root certification or a later ref CAS fails after the durable LMAP completes, the verified compatibility map may remain. This is intentional: it describes real local objects and can safely support a later retry or incremental negotiation.

Fully up-to-date known-only fetches still create no redundant LMAP generation, so they do not run a new durability fence.

## SHA-256-native invariants

- remote/compatibility object identities remain genuine complete 40-hex SHA-1 values;
- local objects and refs remain genuine complete content-derived 64-hex SHA-256 values;
- the LMAP contains only validated native SHA-1 <-> local SHA-256 pairs;
- no SHA-1 padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Tests

`tests/test_phase343.py` verifies that the incremental transaction is bound to the Phase342 durable API, that a durability failure blocks certification/FETCH_HEAD/ref publication, and that known-only completion still skips unnecessary map publication.
