# Phase380: Pin loose-object directory durability fences

Phase380 extends the loose-object success-after-durability contract from file inodes to the directory inodes whose namespaces are being fenced.

## Problem

Phase378 gives both loose-object entry paths a strict file-level rule:

- existing candidates are exact-zlib validated through a pinned object descriptor;
- new publications retain the temporary/object descriptor through atomic replacement;
- success requires the live object pathname to remain correlated with the validated or transaction-owned file inode.

The two namespace durability fences were still pathname-based. `fsync_directory(path)` re-opens the fanout directory and primary `objects/` root each time it is called. A concurrent rename/replacement of either directory can therefore make the durability call act on a different directory inode than the namespace through which the transaction published or validated its object.

Even if the object-file inode is correctly pinned, success must not be justified by fsyncing an unrelated replacement namespace.

## Implementation

On POSIX, `pygit.durable_object_store` now retains descriptors for both:

1. the primary `objects/` directory;
2. the object's two-hex fanout directory.

Each pinned directory records `(st_dev, st_ino)`, is opened with directory/no-follow/cloexec flags where available, and is explicitly made non-inheritable.

The existing `fsync_directory(path)` call remains in place as the mature compatibility/test seam. The new authoritative identity-aware fence then:

1. verifies the live pathname still names the retained directory inode;
2. fsyncs the retained directory descriptor with EINTR retry;
3. verifies the pathname still names that same directory inode afterward.

Both fanout and objects-root identities are checked again at the end of certification/publication. A missing, renamed, or replaced directory therefore makes the current attempt return `False`; the surrounding `ObjectStore.write()` logic falls through to its existing strict certification / atomic republish retry path.

Windows keeps the existing atomic publication semantics. Python does not expose the same POSIX directory-fd fsync contract there, so Phase380 deliberately does not claim a power-loss directory durability guarantee on Windows.

## Existing-object path

The existing candidate now has three simultaneously retained identities:

- object file inode;
- fanout directory inode;
- primary objects-root directory inode.

Exact zlib validation and object fsync happen first. Namespace durability succeeds only when both directory descriptors are fenced and all three pathnames remain correlated with their pinned inodes.

## New-publication path

The fanout/root descriptors are pinned before the temporary publication. The new object descriptor remains retained exactly as in Phase376. After atomic replacement, both directory descriptors are fenced and correlated; success then requires the final object pathname and both directory pathnames to remain on the transaction's retained inodes.

If a directory is swapped during that window, the attempt cannot report success. Any visible winner is handled through Phase378's strict existing-object certification, or the transaction performs another normal atomic publication.

## Regression coverage

`tests/test_phase380.py` covers:

- a pinned directory path being renamed/recreated is rejected;
- retained fanout/root descriptors are non-inheritable and are themselves fsynced;
- new loose-object publication retries when the fanout directory is replaced during its fence;
- existing-object certification repairs into the replacement fanout rather than accepting the old namespace fence;
- replacement of the primary `objects/` directory is detected by inode correlation.

Inherited Phase375/376/378 tests continue to cover strict zlib validation, object inode pinning, competing exact/corrupt file winners, directory-fsync error propagation, and SHA-256-native object identity.

## SHA-256-native invariants

This phase changes only local namespace durability. The object pathname is still the genuine 64-hex SHA-256 of `<type> <size>\0<payload>`. Remote/native interoperability elsewhere still uses genuine complete 40-hex SHA-1 identities where Git requires them. No padding, truncation, textual-id rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase378 / PR #354 head `cb6e4b8c673cd3b64f7a9e7a8160d6e51e11e2d2`;
- Phase378 Tests #3168: Python 3.9 / 3.13 both 2712 passed, Git 2.55.0;
- Phase379 was occupied by unrelated protocol-v2 bundle-uri discovery before this branch was created;
- Phase380 was collision-checked immediately before creation and was free.

This phase intentionally remains a stacked, open, unmerged pull request.
