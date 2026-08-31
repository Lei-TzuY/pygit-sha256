import os

import pytest

from pygit.fetch_head_durable import write_fetch_head_durable


OID = "a" * 64


def test_fetch_head_file_fsync_retries_eintr(tmp_path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    real_fsync = os.fsync
    calls = 0

    def interrupt_once(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise InterruptedError("signal")
        if os.name == "nt":
            return None
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", interrupt_once)

    write_fetch_head_durable(
        pygit_dir,
        {"refs/heads/main": OID},
        source="origin",
        mergeable=("refs/heads/main",),
    )

    assert calls >= 2
    assert (pygit_dir / "FETCH_HEAD").read_text().startswith(OID + "\t\t")
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_fetch_head_directory_fsync_retries_eintr(tmp_path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    real_fsync = os.fsync
    calls = 0

    def interrupt_directory_once(fd):
        nonlocal calls
        calls += 1
        # First call is the lockfile fence. Interrupt the first directory fence.
        if calls == 2:
            raise InterruptedError("signal")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", interrupt_directory_once)

    write_fetch_head_durable(
        pygit_dir,
        {"refs/heads/main": OID},
        source="origin",
    )

    assert calls == 3
    assert (pygit_dir / "FETCH_HEAD").exists()
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()


def test_fetch_head_non_eintr_file_fsync_failure_rolls_back_lock(tmp_path, monkeypatch):
    pygit_dir = tmp_path / ".pygit"
    error = OSError("disk failure")

    def fail_fsync(fd):
        raise error

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError) as excinfo:
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": OID},
            source="origin",
        )

    assert excinfo.value is error
    assert not (pygit_dir / "FETCH_HEAD").exists()
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-fsync contract")
def test_fetch_head_non_eintr_directory_fsync_failure_propagates_after_replace(
    tmp_path, monkeypatch
):
    pygit_dir = tmp_path / ".pygit"
    real_fsync = os.fsync
    calls = 0
    error = OSError("directory durability failure")

    def fail_directory_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise error
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError) as excinfo:
        write_fetch_head_durable(
            pygit_dir,
            {"refs/heads/main": OID},
            source="origin",
        )

    assert excinfo.value is error
    # Replacement happened before the directory durability fence, so the complete
    # new value may already be visible even though durable success is not reported.
    assert (pygit_dir / "FETCH_HEAD").read_text().startswith(OID + "\t")
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()
