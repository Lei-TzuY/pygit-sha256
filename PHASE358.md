# Phase358: Make publication guard ownership fork-safe

Phase358 closes a process-ownership gap left after Phase357 integrated inode-aware release for both repository publication guards and `FETCH_HEAD.state.lock`.

## Problem

Phase353 and Phase356 retain non-inheritable file descriptors so cleanup can prove that a lock pathname still names the inode acquired by the current transaction. That protects against unlink/recreate races inside one process lifecycle.

`FD_CLOEXEC` and `os.set_inheritable(fd, False)` are **exec** boundaries, not **fork** boundaries. A POSIX `fork()` copies the parent's descriptor table and Python memory into the child. Before Phase358, a child inherited:

- the retained ownership descriptors;
- `_PUBLICATION_GUARD_OWNERSHIP`;
- `_FETCH_HEAD_STATE_GUARD_OWNERSHIP`.

If that child later called a normal release helper, the copied registry still proved the same inode identity and could unlink a lock whose real critical section was still running in the parent. A third writer could then acquire the pathname while the parent incorrectly believed its publication guard remained exclusive.

A long-lived child that never called release could also keep the retained inode descriptors open unnecessarily.

## Implementation

`pygit.fork_guard_ownership` installs one `os.register_at_fork(after_in_child=...)` callback when that API exists.

Immediately after a fork, the child callback:

1. looks only at publication modules already present in `sys.modules`;
2. closes every inherited ownership descriptor in the child copy;
3. clears the child copy of both ownership registries;
4. never imports publication modules;
5. never unlinks a lock pathname.

The parent process is untouched. Its original descriptors and registries remain live, so only the parent can release the locks through the established inode-aware Phase353/356 cleanup.

The hook is registered from `pygit.__init__` before normal repository APIs are installed, so a process using the package receives the fork cleanup before these guard registries can be populated.

Platforms without `os.register_at_fork()` retain the existing behavior; they also do not expose the POSIX fork boundary addressed by this phase.

## Regression coverage

`tests/test_phase358.py` covers:

- direct registry cleanup closes owned descriptors and clears the registry;
- a real POSIX `fork()` while both guard classes are owned;
- the child observes both inherited registries already empty;
- inherited ownership descriptors are closed in the child;
- child-side release helpers cannot unlink the parent's live locks;
- child-side reacquisition still fails on the parent's `O_CREAT|O_EXCL` lockfiles;
- the parent retains valid ownership descriptors and can release normally after the child exits.

## Safety and identity invariants

This phase changes only process-local lock ownership bookkeeping. It does not alter Git wire data, object contents, ref values, `FETCH_HEAD`, LMAP encoding, or repository object identity.

- remote/native compatibility identities remain genuine full 40-hex SHA-1 values;
- local objects, refs, and `FETCH_HEAD` remain genuine content-derived full 64-hex SHA-256 values;
- no padding, truncation, identifier-text rehashing, surrogate SHA-256, or metadata-derived identity is introduced.

## Coordination

- actual `main` remains `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- exact base is Phase357 / PR #334 head `59ba41f6eb65f62a450c18a7717bf9b5f84e71e2`;
- Phase357 Tests #3021 completed successfully with Python 3.9 / 3.13 both at 2633 passed on Git 2.55.0;
- Phase358 was collision-checked immediately before branch creation and was free;
- Phase355 is an independent push-tracking line and is untouched.

This phase remains a stacked, open, unmerged change until its own exact-head CI is green.
