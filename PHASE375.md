# Phase375: Stream-validate existing loose-object candidates

Phase375 makes Phase373's existing-object durability certification strict enough to match Git's integrity expectations without giving a corrupt loose file an unbounded decompression budget.

## Problem

Phase373 correctly pinned the inode that it validated and fenced that same inode and its namespaces before reporting existing-object success. Its content check still used whole-file `zlib.decompress()` followed by a SHA-256 comparison.

That leaves two issues:

1. Python's ordinary zlib decompression accepts a valid first stream while ignoring bytes after its end. Native Git's ordinary object reads can also be permissive here, but `git fsck --strict` diagnoses trailing garbage after a loose object as corruption. A write-side durability fast path should not certify a shape that strict integrity checking calls corrupt.
2. whole-file decompression lets a malicious or damaged existing pathname force allocation according to its compressed payload before the writer can decide that the candidate is not the object it is trying to store.

## Implementation

`pygit.durable_object_store` replaces whole-file existing-candidate decompression with `_matches_exact_zlib_stream(fd, expected_store_bytes)`.

The validator:

- reads compressed input in bounded 1 MiB chunks with EINTR retry;
- uses `zlib.decompressobj()` instead of the convenience one-shot decoder;
- limits each decompressor output call to at most 1 MiB;
- never permits total output to exceed the exact expected Git object envelope by even one byte;
- compares every produced chunk byte-for-byte with that expected envelope as it is produced;
- requires the zlib stream to reach its real end marker;
- rejects `unused_data`, which covers trailing bytes and concatenated second zlib streams;
- rejects physical EOF before the zlib stream reaches `eof`;
- rejects a decoder state that makes no progress instead of spinning;
- leaves Phase373's pinned-inode fsync and before/after pathname correlation unchanged.

The writer already has the exact `<type> <size>\0<payload>` bytes from the object it is publishing, so comparing against those bytes gives a tighter and cheaper memory bound than decompressing an arbitrary candidate and hashing the entire result afterward.

## Repair semantics

A non-exact existing stream is not surfaced as a new fatal write error. It simply fails the existing-object certification and falls through to the mature same-directory temporary + file fsync + atomic replace + directory-fence path. Thus a caller writing the correct content repairs:

- trailing garbage;
- a concatenated extra zlib stream;
- a truncated zlib stream;
- content that expands beyond the expected object envelope;
- all corrupt/missing/symlink/raced candidates already covered by Phase373.

Hard I/O or durability failures still propagate rather than being rewritten as corruption.

## Native Git differential

The Phase375 regression suite creates a real SHA-256 Git loose blob, appends garbage after the valid zlib stream, and invokes `git fsck --strict`. Git 2.55.0 must reject that loose object as corrupt. Pygit's write-side existing-object certification adopts this strict integrity boundary while keeping ordinary read compatibility unchanged.

## Regression coverage

`tests/test_phase375.py` covers:

- trailing garbage -> atomic repair;
- concatenated second zlib stream -> atomic repair;
- truncated zlib stream -> atomic repair;
- decompressed output beyond the expected envelope -> atomic repair;
- an explicit proxy assertion that every decompressor call is bounded by `_OUTPUT_CHUNK`;
- native SHA-256 Git strict-fsck rejection of trailing loose-object garbage.

Inherited Phase373 tests continue to cover durability retry healing, inode/path correlation, read EINTR, symlink refusal, and concurrent pathname replacement.

## SHA-256-native invariants

The expected bytes remain the Git-compatible object envelope, and the published pathname remains its genuine 64-hex SHA-256 identity. This phase changes only strict validation of a candidate already occupying that pathname. It does not derive local identity from metadata or from remote SHA-1 text.

## Coordination

- actual `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase373 / PR #350 head `085a2994553c94082464310b989e3801d1586a5d`;
- Phase373 Tests #3143 / run `33459136214`: Python 3.9 / 3.13 both 2697 passed, Git 2.55.0;
- Phase374 was occupied by the unrelated clone-origin retarget line before this branch was created;
- Phase375 was collision-checked immediately before creation and was free.

This phase intentionally remains a stacked, open, unmerged pull request.
