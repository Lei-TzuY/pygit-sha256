# Phase 342 — durable Git LMAP publication

Phase341 made Git-compatible LMAP v1 publication atomic, conflict-free, and serialized across concurrent writers. Phase342 adds a separate success-after-durability boundary for callers that need the map generation to survive a process/host crash once the API reports success.

## API

`publish_staged_loose_object_map_durable(repo, staged)` composes the existing Phase341 writer rather than replacing it.

The ordering is:

1. authenticate every staged local object by its complete content-derived SHA-256;
2. encode and fsync the temporary Git LMAP v1 file;
3. hold Phase341's repository-wide `objects/object-map/publish.lock` while checking all existing generations for SHA-1/SHA-256 conflicts;
4. publish the immutable content-addressed `map-<sha256>.map` entry atomically;
5. release the writer lock;
6. fsync `objects/object-map`;
7. fsync `objects` so creation of the `object-map` directory itself is also fenced;
8. only then return success.

The final two directory fences are the Phase342 addition. On POSIX filesystems, syncing file contents alone is not sufficient to promise that a newly linked filename survives sudden power loss; the containing directory namespace must also be synced. The parent fence covers the first creation of the `object-map` directory.

## Failure model

A directory-fsync failure is propagated and is **not** converted into success. The map may already be visible because Phase341 intentionally publishes immutable metadata before the durability fence. That state is safe to retain: publication is content-addressed and idempotent, so retrying validates and reuses the same generation before repeating the durability fences.

Phase342 does not roll back an already-valid map generation after a durability error. Unlinking it would introduce another namespace mutation requiring its own durability ordering and could race readers; retaining verified immutable metadata is the safer failure state.

On Windows, Python does not expose the same portable directory-file-descriptor fsync contract. `_fsync_directory()` is therefore an explicit no-op there rather than pretending to provide a POSIX power-loss guarantee. Phase341's atomic/content-authenticated publication semantics still apply.

## SHA-256-native invariants

Nothing about object identity changes:

- remote/compatibility identities are genuine full 40-hex SHA-1 values;
- local object/ref identities are genuine full content-derived 64-hex SHA-256 values;
- LMAP continues to store only validated SHA-1 ↔ SHA-256 pairs;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Tests

`tests/test_phase342.py` covers exact fence ordering, real LMAP round-trip lookup, propagated durability failure after safe immutable publication, idempotent retry, descriptor cleanup when `fsync` fails, and the explicit Windows no-op boundary.

Phase342 intentionally leaves the older Phase341 API unchanged. A subsequent phase can move the incremental fetch transaction to this stronger durability-return boundary after validating the desired transaction-level failure semantics around refs and `FETCH_HEAD`.
