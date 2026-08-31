# Phase356: Preserve FETCH_HEAD state-guard release ownership

Phase356 closes the cleanup-side ownership gap in Phase348/354's short-lived `FETCH_HEAD.state.lock`.

## Problem

Phase354 made state-lock setup fail closed across descriptor hardening, short writes, `EINTR`, `fsync()`, and setup-descriptor close. Release, however, still accepted only a pathname and unconditionally unlinked it.

If an external actor removes the original pathname and creates a replacement before the first fetch reaches cleanup, path-only release can delete a lock that belongs to the replacement writer. Comparing marker bytes is not sufficient because a legitimate replacement uses the same canonical marker.

## Implementation

The public/internal Path-shaped seam is preserved: `_acquire_fetch_head_state_guard()` still returns the canonical `Path`, and its original setup descriptor still closes before return.

Before that close, Phase356:

1. duplicates the initialized descriptor;
2. explicitly makes the duplicate non-inheritable;
3. captures `st_dev` and `st_ino` from the duplicate;
4. retains that descriptor in `_FETCH_HEAD_STATE_GUARD_OWNERSHIP` until release.

The retained descriptor pins the original inode while the fetch owns the state guard. `_release_fetch_head_state_guard(path)` removes the ownership record, performs `stat(..., follow_symlinks=False)` on the live pathname, and unlinks only when `(st_dev, st_ino)` still matches the retained descriptor identity. Missing or replaced pathnames are left untouched. The retained descriptor is closed on every release path.

A process-local ownership record also blocks reacquiring the same canonical path if an external actor removed the pathname while the original owner is still active. This prevents one process from accumulating two ambiguous ownership tokens for one `Path` return value. Other processes remain coordinated by the canonical `O_CREAT|O_EXCL` pathname.

## Compatibility

The existing Phase348/354 call shape remains unchanged:

- acquire returns a `Path`;
- release accepts that `Path`;
- the setup descriptor is closed before successful acquire returns;
- the lock filename and marker bytes are unchanged;
- early-clear and final-publication lock lifetimes are unchanged.

This phase adds only ownership bookkeeping and release discipline. It does not broaden the state-lock critical section or change FETCH_HEAD publication semantics.

## Failure model

- failure to duplicate or harden the retained ownership descriptor fails acquisition and removes only the transaction-created path;
- a foreign pre-existing pathname still fails closed and is never stolen;
- a missing owned pathname at release closes the retained descriptor without recreating or unlinking anything;
- a replacement pathname, including one containing the exact canonical marker, is preserved;
- release without a recorded ownership token is a no-op rather than risking deletion of an unowned file.

As with Git-style lockfiles generally, this is a cooperative lock protocol rather than a defense against arbitrary hostile directory mutation. The retained descriptor closes the deterministic stale-cleanup hole by refusing to unlink a path already known not to be the acquired inode.

## Tests

`tests/test_phase356.py` covers:

- retained non-inheritable descriptor identity;
- normal owned-inode removal and descriptor closure;
- arbitrary replacement preservation;
- canonical-marker replacement preservation;
- missing-path release;
- unowned-path no-op behavior;
- same-process reacquire rejection while the old inode remains owned;
- retained-descriptor duplication failure cleanup.

Inherited Phase348 multiprocess contention and Phase354 setup-fault tests remain authoritative for the complete lifecycle.

## SHA-256-native invariants

Phase356 changes only local lock ownership metadata. Remote/native compatibility identities remain genuine complete 40-hex SHA-1 values. Local objects, refs, and `FETCH_HEAD` remain genuine complete content-derived 64-hex SHA-256 values. No padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base: Phase354 / PR #331 head `05d1250eed16c6150af5c7c0f9446488d969c576`;
- Phase354 authoritative Tests #2993: Python 3.9 / 3.13 both 2617 passed, Git 2.55.0;
- Phase353 independently applies the same inode-ownership principle to repository-wide publication guards, while Phase356 is limited to `FETCH_HEAD.state.lock`;
- Phase355 was already occupied by independent push-tracking work;
- Phase356 was collision-checked immediately before creation.

This phase intentionally remains an open, unmerged stacked pull request.
