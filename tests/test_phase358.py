from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import pygit.fork_guard_ownership as fork_guard
import pygit.protocol_v2_packfile_uri_incremental_fetch as incremental_fetch
import pygit.protocol_v2_packfile_uri_transaction as transaction
from pygit.repo import Repository


def test_close_and_clear_registry_closes_owned_descriptors() -> None:
    read_fd, write_fd = os.pipe()
    registry = {
        "owned": SimpleNamespace(fd=read_fd),
        "ignored": SimpleNamespace(fd="not-an-fd"),
    }
    try:
        fork_guard._close_and_clear_registry(registry)
        assert registry == {}
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        os.close(write_fd)


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "register_at_fork"),
    reason="fork ownership regression requires POSIX fork hooks",
)
def test_fork_child_cannot_release_parent_guard_ownership(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    state_lock = incremental_fetch._acquire_fetch_head_state_guard(repo.pygit_dir)
    publication_locks = transaction._acquire_publication_guard_locks(repo)

    state_fd = incremental_fetch._FETCH_HEAD_STATE_GUARD_OWNERSHIP[state_lock].fd
    publication_fds = tuple(
        transaction._PUBLICATION_GUARD_OWNERSHIP[path].fd
        for path in publication_locks
    )
    read_fd, write_fd = os.pipe()

    try:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            payload = b"ok"
            exit_code = 0
            try:
                assert incremental_fetch._FETCH_HEAD_STATE_GUARD_OWNERSHIP == {}
                assert transaction._PUBLICATION_GUARD_OWNERSHIP == {}

                for inherited_fd in (state_fd, *publication_fds):
                    try:
                        os.fstat(inherited_fd)
                    except OSError:
                        pass
                    else:
                        raise AssertionError(
                            f"fork child retained ownership fd {inherited_fd}"
                        )

                incremental_fetch._release_fetch_head_state_guard(state_lock)
                transaction._release_publication_guard_locks(publication_locks)
                assert state_lock.exists()
                assert all(path.exists() for path in publication_locks)

                try:
                    incremental_fetch._acquire_fetch_head_state_guard(repo.pygit_dir)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("fork child reacquired parent FETCH_HEAD state lock")

                try:
                    transaction._acquire_publication_guard_locks(repo)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("fork child reacquired parent publication guards")
            except BaseException as exc:
                payload = ("error: " + repr(exc)).encode("utf-8", "replace")
                exit_code = 1

            try:
                os.write(write_fd, payload)
            finally:
                os.close(write_fd)
            os._exit(exit_code)

        os.close(write_fd)
        write_fd = -1
        child_message = os.read(read_fd, 8192)
        _, status = os.waitpid(pid, 0)

        assert os.WIFEXITED(status), child_message.decode("utf-8", "replace")
        assert os.WEXITSTATUS(status) == 0, child_message.decode("utf-8", "replace")
        assert child_message == b"ok"

        assert state_lock in incremental_fetch._FETCH_HEAD_STATE_GUARD_OWNERSHIP
        assert all(
            path in transaction._PUBLICATION_GUARD_OWNERSHIP
            for path in publication_locks
        )
        os.fstat(state_fd)
        for fd in publication_fds:
            os.fstat(fd)
        assert state_lock.exists()
        assert all(path.exists() for path in publication_locks)
    finally:
        if write_fd != -1:
            try:
                os.close(write_fd)
            except OSError:
                pass
        try:
            os.close(read_fd)
        except OSError:
            pass
        transaction._release_publication_guard_locks(publication_locks)
        incremental_fetch._release_fetch_head_state_guard(state_lock)

    assert not state_lock.exists()
    assert all(not path.exists() for path in publication_locks)
