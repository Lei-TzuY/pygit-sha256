# Phase 341 — serialize conflict-free Git LMAP publication

Phase 341 closes the writer-side race left after the Phase330/332 Git-compatible loose-object map implementation.

## Problem

`objects/object-map/map-*.map` generations are immutable and content addressed, and the existing reader already rejects contradictory generations. That alone is not sufficient for concurrent fetches: two writers can each inspect a conflict-free repository state and then publish different individually valid LMAP files whose SHA-1 ↔ SHA-256 identities contradict each other.

A later reader would detect the corruption, but by then the repository would already contain a contradictory compatibility namespace.

## Implementation

`publish_staged_loose_object_map()` now uses one repository-wide writer lock at:

`objects/object-map/publish.lock`

The function still authenticates every staged local object against its content-derived 64-hex SHA-256 before entering the critical section. Once the lock is held it:

1. reads and validates every existing immutable LMAP generation;
2. rejects an existing native SHA-1 mapped to a different local SHA-256;
3. rejects an existing local SHA-256 mapped to a different native SHA-1;
4. performs the existing content-addressed hard-link publication while still holding the lock;
5. releases only the lock it acquired, including error paths.

The lock is acquired with exclusive create. An already-existing lock is never overwritten, removed, or treated as stale automatically; publication fails closed instead.

Readers remain lock-free because a complete map generation is immutable and only becomes visible through the existing atomic hard-link publication step.

## SHA-256-native boundary

This phase does not translate identities. Remote compatibility identities remain full 40-hex SHA-1 values and local storage/ref identities remain full content-derived 64-hex SHA-256 values. The lock only serializes publication of already validated identity pairs.

No SHA-1 padding, truncation, re-hashing of object-id text, surrogate SHA-256, or metadata-derived local identity is introduced.

## Compatibility and transaction semantics

The on-disk LMAP v1 format is unchanged. Existing readers and Git-compatible map files remain valid. The change is writer coordination around the existing immutable format, not a new file format.

If a fetch later fails root certification or ref CAS, a successfully published LMAP remains safe immutable compatibility metadata for already materialized objects. If LMAP publication itself detects contention or a cross-generation identity conflict, it fails before any new map generation is published and before the higher-level transaction reaches ref publication.

## Tests

`tests/test_phase341.py` covers:

- multiple consistent immutable generations;
- native SHA-1 remapping rejection;
- local SHA-256 alias rejection;
- fail-closed handling of an existing writer lock without stealing or deleting it;
- idempotent republication under the serialized preflight;
- lock cleanup on successful and rejected publications.

Phase340's native `FETCH_HEAD` probe was also corrected before this branch was created: the probe now treats native Git's multi-ref FETCH_HEAD ordering as unspecified and asserts that the newly fetched `main` record is present rather than requiring it to be the first line.
