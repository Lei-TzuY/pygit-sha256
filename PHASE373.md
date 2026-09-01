# Phase373: Certify existing loose-object durability

Phase373 closes a retry hole in Phase370's success-after-durability SHA-256 loose-object writer.

## Problem

Phase370 durably publishes a new loose object as:

`temp write -> file fsync -> atomic replace -> fanout fsync -> objects-root fsync -> success`

However, its existing-valid-object fast path returned immediately after content validation. That could report success without a complete durability boundary in two important cases:

1. a previous write had already completed `replace()` but then failed while fsyncing the fanout or `objects/` directory; a retry saw the visible valid object and returned success without retrying the missing namespace fence;
2. a repository created before the durable writer was installed could contain a valid loose object whose file and namespace durability had never been certified by the new contract.

The old validation also happened by pathname separately from any durability operation, leaving a validation-to-fsync TOCTOU window.

## Implementation

`pygit.durable_object_store` now treats an existing candidate as a durability-certification path rather than a metadata-only fast path.

The certification sequence is:

1. open the candidate read-only with close-on-exec and `O_NOFOLLOW` when available;
2. mark the descriptor non-inheritable;
3. pin `(st_dev, st_ino)` with `fstat()` and require a regular file;
4. read the compressed payload from that exact descriptor, retrying `InterruptedError`;
5. decompress and verify the SHA-256 identity against the requested object id;
6. fsync the same descriptor through the shared EINTR-safe `_fsync_retry()` helper;
7. verify the live pathname still names the pinned inode;
8. fsync the fanout directory and the primary `objects/` directory;
9. verify the pathname still names the pinned inode before reporting success.

If the file is missing, corrupt, a symlink on platforms with `O_NOFOLLOW`, or its pathname changes during certification, the helper returns `False` and the mature Phase370 same-directory-temp + atomic-replace publication path repairs/publishes the requested object normally.

A hard file/directory durability error still propagates. Visibility alone is not converted into success.

## Why both inode checks matter

The first pathname/inode check prevents fencing a descriptor that has already been detached from the object-store namespace. The second check detects replacement while the directory fences are in progress. A concurrent replacement therefore does not get certified accidentally; the caller falls back to atomic publication of the content whose SHA-256 identity it is trying to store.

This does not claim to defend against an adversary that can continuously mutate repository files after the final check. It closes the normal concurrent-writer TOCTOU window while preserving the content-addressed publication model.

## Regression coverage

`tests/test_phase373.py` covers:

- healing a previous post-replace objects-root fsync failure without republishing the visible object;
- surfacing an existing-object file-fsync failure instead of reporting success;
- retrying interrupted reads;
- detecting pathname replacement during certification and falling back to atomic publication;
- refusing to trust a symlink as the existing-object fast path on POSIX;
- repairing a corrupt existing loose-object pathname through normal publication.

Phase370's inherited existing-valid-object test is updated to assert the new contract: no republish occurs, but the file, fanout, and objects-root durability boundaries are re-certified.

## SHA-256-native invariants

Nothing about object identity changes:

- local loose objects remain Git-compatible `<type> <size>\0<payload>` envelopes;
- the path remains the genuine 64-hex SHA-256 of that envelope;
- native Git differential identity behavior from Phase370 remains unchanged;
- no SHA-1 padding, truncation, textual-id rehashing, surrogate SHA-256, or metadata-derived object identity is introduced.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase370 / PR #347 head `ebbbbf7ec278c6ab13f79f42553525c5cd08a83d`;
- Phase370 Tests #3130 / run `33457783654`: Python 3.9 / 3.13 both 2691 passed, Git 2.55.0;
- Phase371 and Phase372 are clone/unborn work and are intentionally not part of this durability continuation;
- Phase373 was collision-checked immediately before creation and was free.

This phase intentionally remains a stacked, open, unmerged pull request.
