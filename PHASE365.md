# Phase365 — integrate durable owned-lock release from the last green base

Phase363 and Phase364 were found to be red after their exact-head GitHub Actions runs completed. Phase363 changed the batch helper in a way that invalidated inherited Phase362 behavioral regressions, and Phase364 stacked on that red base. Phase365 therefore rebuilds the production integration cleanly from the last exact-green Phase362 head instead of extending or overwriting the red line.

## Production integration

At package initialization, `install_durable_owned_lock_release_integration()` binds the three retained ownership registries in the protocol-v2 packfile-URI publication stack to the exact-green Phase361/362 primitives:

- repository publication guards -> `release_owned_locks_durably()`;
- `FETCH_HEAD.state.lock` -> `release_owned_lock_durably()`;
- target canonical ref locks -> `release_owned_locks_durably()`.

Each registry keeps its existing acquisition dataclass and Path-shaped private caller seam. At release, retained `fd`, `st_dev`, and `st_ino` values are converted to `OwnedLockIdentity`. Registry ownership is popped before cleanup so a post-unlink durability error cannot strand stale in-process ownership state.

## Rollback durability

The shared primitive also applies when publication-guard acquisition rolls back a guard that was successfully initialized before a later guard fails. On POSIX, that cleanup now adds a parent-directory `fsync` after unlink. The historical Phase349 fault-injection regression is updated only for this intentional new durability event: two initialization fsync calls plus one rollback directory fence. Windows preserves the existing no-directory-fsync boundary.

## Compatibility

This phase deliberately does **not** include Phase363's sibling-directory-fsync coalescing optimization. The exact-green Phase362 reverse-order batch semantics remain intact, including its monkeypatch-visible call structure and first-error behavior. The focus is production adoption of already-green primitives, not optimization on top of a red base.

No Git-visible lock names, markers, refs, refspecs, FETCH_HEAD format, object-map format, or protocol behavior changes. Acquisition remains `O_CREAT|O_EXCL` with retained non-inheritable ownership descriptors, and replacement/missing pathnames remain protected by inode identity checks.

## SHA-256-native invariants

Remote/native compatibility identity remains genuine full 40-hex SHA-1. Local objects, refs, reflogs, FETCH_HEAD, and object-map identities remain genuine content-derived full 64-hex SHA-256. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived local identity is introduced.
