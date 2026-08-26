# Phase 115 — atomic loose-object publication

Phase 115 hardens the core SHA-256 object store so creating a loose object can no longer expose a partially written final object file.

## Problem

`ObjectStore.write()` previously compressed an object and called `Path.write_bytes()` directly on its final content-addressed path. A process crash, I/O failure, or interrupted write could therefore leave `.pygit/objects/xx/<62-hex>` present but truncated or otherwise corrupt. Because the path itself is the object identity, later readers would encounter a corrupt object at the authoritative loose-object location.

## Atomic publication

Loose writes now use a same-directory temporary file:

1. build and SHA-256 hash the complete Git-style object envelope;
2. compress the complete envelope with zlib;
3. create a unique temporary file beside the final object;
4. write all compressed bytes, flush, and `fsync` the temporary file;
5. atomically install it with `os.replace()`;
6. remove any leftover temporary file on failure.

The final content-addressed pathname therefore appears only after the complete compressed payload has reached the publication boundary. If `fsync` or replacement fails, the error propagates and no partial new target is exposed.

## Existing-object behavior

Content-addressed idempotence remains intact. A valid existing object is checked against its SHA-256 name and returns immediately without creating a temporary file.

If the final loose-object path already exists but contains invalid zlib bytes or bytes whose SHA-256 does not match the requested object ID, writing the same object repairs that damaged path through the atomic replacement flow instead of silently preserving corruption.

Concurrent writers of identical content remain safe: each temporary file is unique and every successful replacement publishes bytes for the same SHA-256 object identity.

## Enumeration safety

Temporary files live inside the normal two-hex loose-object directory so replacement stays on the same filesystem. `ObjectStore.all_shas()` therefore now recognizes only canonical lowercase `2 + 62` hexadecimal loose-object filenames. Atomic temporary files and unrelated junk files are ignored rather than being misreported as object IDs.

## Compatibility boundary

The object envelope, SHA-256 identity, zlib encoding, final loose-object path, packed-object lookup, and multi-pack-index behavior are unchanged. This phase changes only loose-object publication and loose-file enumeration safety.

## Regression coverage

`tests/test_phase115.py` covers valid-object idempotence, repair of corrupt existing objects, simulated `fsync` and `os.replace` failures, temporary-file cleanup, concurrent identical writers, and filtering of temporary/junk files from loose-object enumeration.
