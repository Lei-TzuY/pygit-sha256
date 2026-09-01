# Phase370 — Durable SHA-256 loose-object publication

Phase370 moves the shared success-after-durability discipline from packfile-URI metadata into the repository's primary SHA-256 content-addressed object store.

## Problem

`ObjectStore.write()` already used a strong basic publication shape:

`same-directory temp -> write compressed object -> flush -> fsync -> atomic replace`

Three durability details needed to be explicit:

1. the file fence used one direct `os.fsync()` call, so a transient POSIX `InterruptedError` could abort an otherwise complete object write;
2. after `os.replace()` made the new loose-object pathname visible, the containing two-hex fanout directory was not fsynced before `write()` reported success;
3. fsyncing the fanout itself is not enough to prove its directory entry in `objects/` is durable. A fanout can also remain visibly present after an earlier objects-root fence failed, so mere pre-existence cannot be used as durable evidence.

These distinctions matter for crash semantics: a complete file can be visible in the current process while one or more namespace updates are not yet guaranteed durable across an unclean shutdown.

## Implementation

A dedicated `pygit.durable_object_store` installer replaces `ObjectStore.write()` without changing its public signature, hash computation, compression format, alternate-store rules, or existing-object fast path.

The durable write order is:

1. build the canonical Git object envelope;
2. compute the existing local `HASH_ALGO` content identity;
3. return immediately if a valid object already occupies the content-addressed path;
4. create the two-hex fanout when needed and create a same-directory temporary inside it;
5. write and flush the zlib-compressed object;
6. durability-fence the file through shared `_fsync_retry(fd)`;
7. atomically replace the final content-addressed pathname;
8. on POSIX, fsync the containing fanout directory through shared `fsync_directory()`;
9. on POSIX, fsync the `objects/` root on every successful publication so a previous failed/unknown fanout-entry fence cannot be inherited as trusted state;
10. return the SHA-256 object id only after all applicable durability fences succeed.

Windows preserves the project's existing boundary: the object file is fsynced, while no POSIX directory-fd durability claim is made.

A pre-replace durability failure removes the temporary and exposes no new object. A post-replace fanout/root directory-fsync failure propagates even though the complete object may already be visible; the caller is not told that publication durably succeeded. A later write re-fences both namespace levels rather than treating a visible fanout as proof that the earlier failed root fence became durable.

The installer is activated through the package's existing ObjectStore extension hook before the promisor-aware read wrapper is layered. This follows the project's established `install_*_support()` pattern while avoiding a rewrite of the mature store reader/pack/alternate code.

## Regression coverage

`tests/test_phase370.py` verifies:

- loose-object file fsync retries EINTR before success;
- publication executes file -> fanout directory -> objects-root durability fences;
- even a pre-existing fanout still receives the objects-root ancestry fence;
- a non-EINTR file-fsync failure leaves no published loose object;
- a post-replace fanout-directory fsync failure propagates while leaving only the complete content-addressed object visible;
- an objects-root fsync failure also propagates without falsely reporting durable success;
- writing an already-valid object takes the existing zero-publication fast path and performs no new fsync;
- a native SHA-256 Git `hash-object` differential produces exactly the same 64-hex blob identity as the durable pygit writer.

## Git compatibility and identity invariants

The durable writer does not alter object bytes. Local loose objects remain standard Git-style `<type> <size>\0<payload>` envelopes compressed with zlib and addressed by the repository's SHA-256 hash algorithm. The native differential guards that identity boundary directly.

Remote/native compatibility identities elsewhere remain genuine full 40-hex SHA-1 values where protocol interoperability requires them. No padding, truncation, object-id text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- exact base: Phase369 / PR #346 head `e800ef9364019c1cb2d013f4fc94f600dcb4b771`;
- Phase369 Tests #3104 / run `33456806013`: success;
- Python 3.9 / 3.13 on the base: 2683 passed each;
- CI Git on the base: 2.55.0;
- Phase370 and Phase371 namespaces were collision-checked immediately before branch creation;
- this phase intentionally remains stacked, open, and unmerged.
