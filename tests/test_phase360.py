from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_refs as refpub
from pygit import Repository
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.refs import ZERO_SHA


TARGET = "refs/remotes/origin/main"
NATIVE = "d" * 40
MARKER = b"packfile-uri ref transaction\n"
REPLACEMENT = b"replacement target ref lock\n"


def _commit(repo: Repository) -> str:
    path = repo.worktree / "phase360.txt"
    path.write_text("target ref ownership\n", encoding="utf-8")
    repo.add(["phase360.txt"])
    return repo.commit("phase360 target ref ownership")


def test_target_ref_lock_completes_short_writes_and_retains_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    real_write = refpub.os.write
    calls = 0

    def tiny_write(fd: int, data) -> int:
        nonlocal calls
        calls += 1
        payload = bytes(data)
        return real_write(fd, payload[:4])

    monkeypatch.setattr(refpub.os, "write", tiny_write)

    locks = refpub._acquire_locks(repo, [TARGET])
    try:
        assert calls > 1
        assert len(locks) == 1
        lock = locks[0]
        assert lock.read_bytes() == MARKER
        owner = refpub._REF_LOCK_OWNERSHIP[lock]
        os.fstat(owner.fd)
        assert os.get_inheritable(owner.fd) is False
    finally:
        refpub._release_locks(locks)

    assert not refpub._REF_LOCK_OWNERSHIP
    assert all(not lock.exists() for lock in locks)


def test_zero_progress_target_ref_marker_write_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    lock = refpub._lock_path(repo, TARGET)
    monkeypatch.setattr(refpub.os, "write", lambda fd, data: 0)

    with pytest.raises(OSError, match="ref lock marker write made no progress"):
        refpub._acquire_locks(repo, [TARGET])

    assert not lock.exists()
    assert not refpub._REF_LOCK_OWNERSHIP


def test_replaced_target_ref_lock_survives_owner_release(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    locks = refpub._acquire_locks(repo, [TARGET])
    lock = locks[0]
    owner = refpub._REF_LOCK_OWNERSHIP[lock]
    owned_fd = owner.fd

    lock.unlink()
    lock.write_bytes(REPLACEMENT)
    replacement_stat = os.stat(lock, follow_symlinks=False)
    assert (replacement_stat.st_dev, replacement_stat.st_ino) != (owner.device, owner.inode)

    refpub._release_locks(locks)
    try:
        assert lock.read_bytes() == REPLACEMENT
        assert lock not in refpub._REF_LOCK_OWNERSHIP
        with pytest.raises(OSError):
            os.fstat(owned_fd)
    finally:
        lock.unlink()


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "register_at_fork"),
    reason="target ref ownership regression requires POSIX fork hooks",
)
def test_fork_child_cannot_release_parent_target_ref_lock(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    locks = refpub._acquire_locks(repo, [TARGET])
    lock = locks[0]
    owned_fd = refpub._REF_LOCK_OWNERSHIP[lock].fd
    read_fd, write_fd = os.pipe()

    try:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            payload = b"ok"
            exit_code = 0
            try:
                assert refpub._REF_LOCK_OWNERSHIP == {}
                try:
                    os.fstat(owned_fd)
                except OSError:
                    pass
                else:
                    raise AssertionError("fork child retained target-ref ownership fd")

                refpub._release_locks(locks)
                assert lock.exists()
                try:
                    refpub._acquire_locks(repo, [TARGET])
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("fork child reacquired parent target ref lock")
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

        assert lock in refpub._REF_LOCK_OWNERSHIP
        os.fstat(owned_fd)
        assert lock.exists()
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
        refpub._release_locks(locks)

    assert not lock.exists()
    assert not refpub._REF_LOCK_OWNERSHIP


def test_ref_durability_fence_runs_with_owned_target_lock(tmp_path: Path, monkeypatch) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    certificate = PackfileUriRootCertificate({NATIVE: tip}, {NATIVE: b"commit"})
    publication = {TARGET: PackfileUriRefPublication(NATIVE, ZERO_SHA)}
    seen_fds: list[int] = []

    def assert_owned(path: Path) -> None:
        lock = refpub._lock_path(repo, TARGET)
        owner = refpub._REF_LOCK_OWNERSHIP[lock]
        os.fstat(owner.fd)
        assert lock.exists()
        seen_fds.append(owner.fd)

    monkeypatch.setattr(refpub, "_fsync_file", assert_owned)
    monkeypatch.setattr(refpub, "_fsync_directory", lambda path: None)

    result = refpub.publish_packfile_uri_refs(repo, certificate, publication)

    assert result == {TARGET: tip}
    assert seen_fds
    assert not refpub._REF_LOCK_OWNERSHIP
    assert not refpub._lock_path(repo, TARGET).exists()
