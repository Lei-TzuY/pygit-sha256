# Phase362 — durable batch owned-lock cleanup

Phase361 introduced a reusable inode-aware, success-after-durability release primitive for one retained lock descriptor. Phase362 extends that boundary to callers that own several canonical lockfiles at once.

## Why this phase exists

Repository publication guards and target-ref publication both acquire more than one lock. A single-lock release primitive is safe for one pathname, but a caller that simply loops and stops on the first directory-fsync error can strand locks that were acquired earlier in the transaction. That turns one durability failure into persistent-looking future lock contention.

`release_owned_locks_durably()` therefore applies the same ownership/durability contract to a batch while preserving cleanup progress:

1. release in reverse acquisition order;
2. for each lock, compare the live non-following pathname identity with the retained descriptor `(st_dev, st_ino)`;
3. never unlink a missing or replacement pathname;
4. when the owned pathname is removed, fsync its parent directory on POSIX before that individual release is considered durable;
5. close every retained ownership descriptor on every path;
6. if any release/fence fails, remember the first exception but continue processing all remaining locks;
7. re-raise the first exception only after sibling cleanup has completed.

This keeps error reporting deterministic while ensuring one failed durability fence does not prevent cleanup of unrelated locks already owned by the same transaction.

## Compatibility and SHA domains

This phase changes no Git-visible ref, FETCH_HEAD, LMAP, lockfile marker, or transport format. It only strengthens local cleanup semantics.

- remote/native compatibility identities remain genuine full 40-hex SHA-1 values;
- local objects, refs, reflogs, FETCH_HEAD and object-map identities remain genuine content-derived full 64-hex SHA-256 values;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.

Windows retains the explicit existing boundary: Python does not expose the same portable directory-fd fsync guarantee, so the directory durability fence remains a no-op there while inode-aware/atomic ownership behavior is preserved.

## Regression coverage

`tests/test_phase362.py` verifies:

- reverse-acquisition release order;
- sibling cleanup continues after a directory-fsync failure;
- every retained descriptor closes even on failure;
- foreign replacement pathnames survive while other owned locks are released;
- a missing pathname does not block sibling cleanup;
- when several fences fail, the first exception is the one propagated after all cleanup attempts run.

## Coordination

Phase362 was created from the exact Phase361 head `790735a51a8245090a48ceb708d2a486809012da` after confirming Phase360 and Phase361 GitHub Actions were both green and no `phase362` branch existed. It intentionally remains stacked and unmerged.

The next integration step is to replace the hand-rolled multi-lock cleanup loops in repository publication guards and target-ref publication with this batch primitive, then move the single `FETCH_HEAD.state.lock` release onto the Phase361 single-lock primitive.
