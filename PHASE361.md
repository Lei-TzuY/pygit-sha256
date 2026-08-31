# Phase361: Durable inode-aware lock release primitive

Phase361 adds one reusable durability boundary for the three descriptor-owned lock layers in the incremental packfile-URI publication stack.

## Problem

Phases353, 356 and 360 retain a descriptor plus `(st_dev, st_ino)` so cleanup can prove that a canonical lock pathname still names the inode acquired by the current transaction. That closes the deterministic ABA/path-replacement hole.

The remaining common durability gap is namespace removal: after a transaction unlinks its own lock, a crash before the containing directory metadata reaches stable storage can resurrect the lock pathname even though the transaction had already reported cleanup success.

The existing durable LMAP, FETCH_HEAD and ref-publication boundaries already use parent-directory fsync after namespace changes. Lock cleanup should use the same success-after-durability discipline.

## Implementation

`pygit.durable_owned_lock` introduces:

- `OwnedLockIdentity(fd, device, inode)`;
- `fsync_directory(path)`;
- `release_owned_lock_durably(path, ownership)`.

Release semantics are intentionally strict:

1. inspect the current pathname with `follow_symlinks=False`;
2. compare `(st_dev, st_ino)` against the retained descriptor identity;
3. preserve a missing or replaced pathname;
4. unlink only an exact owned inode;
5. fsync the parent directory after a successful unlink on POSIX;
6. close the retained descriptor on every path;
7. propagate a post-unlink directory-fsync error instead of reporting durable cleanup success.

Identical lock marker bytes are not evidence of ownership. Only descriptor-backed filesystem identity is accepted.

Windows keeps the existing atomic/inode-aware semantics but deliberately does not claim a POSIX directory-fd power-loss guarantee, matching the platform boundary already used by durable LMAP and FETCH_HEAD publication.

## Why this phase is isolated

Phase360 was already occupied by parallel work when this phase started. To avoid rewriting or racing the just-landed target-ref ownership work, Phase361 provides the shared primitive as an additive exact-stack layer. A follow-up phase can replace the three duplicated release implementations with this single boundary while preserving each module's existing compatibility seams.

## Regression coverage

`tests/test_phase361.py` verifies:

- matching owned inode -> unlink -> parent-directory fence;
- replacement inode preservation;
- missing pathname preservation;
- descriptor closure on every path;
- directory-fsync failure propagation after a successful unlink;
- identical-byte replacement locks are still foreign;
- the directory descriptor is closed even when fsync fails.

## SHA-256-native invariants

This phase changes only local lock cleanup durability. Remote/native compatibility identities remain genuine full 40-hex SHA-1 values. Local objects, refs, reflogs, FETCH_HEAD and object-map identities remain genuine content-derived full 64-hex SHA-256 values. No padding, truncation, identifier-text rehashing, surrogate SHA-256 or metadata-derived local identity is introduced.

## Coordination

- actual `main` at start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- Phase359 / PR #336 exact head `d193f1c2ad1d61432f7a94a69d5a01612361626b` was authoritative-green;
- Phase360 branch `phase360-own-target-ref-locks` was already occupied and based on Phase359;
- Phase361 stacks on Phase360 exact head `4be244c7470cfcbd807ded4d6b3c4b18c71985af` without modifying Phase360 files;
- Phase361 namespace was collision-checked before creation.

This phase remains open and unmerged until exact-head CI is verified.
