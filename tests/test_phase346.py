from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from pygit.fetch_head_durable import (
    _acquire_fetch_head_lock,
    _render_fetch_head,
    write_fetch_head_durable,
)


def _hold_fetch_head_lock(
    pygit_dir: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    root = Path(pygit_dir)
    fd, lock_path = _acquire_fetch_head_lock(root)
    try:
        os.write(fd, b"foreign-writer")
        ready.set()
        if not release.wait(15):
            raise RuntimeError("timed out waiting to release FETCH_HEAD.lock")
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _burst_writer(
    pygit_dir: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
    index: int,
) -> None:
    refname = f"refs/heads/topic-{index}"
    oid = f"{index + 1:064x}"
    if not start.wait(15):
        results.put(("error", index, "start timeout"))
        return
    try:
        write_fetch_head_durable(
            Path(pygit_dir),
            {refname: oid},
            source="origin",
            mergeable=(refname,),
        )
    except FileExistsError:
        results.put(("contended", index, ""))
    except Exception as exc:  # pragma: no cover - surfaced by parent assertion
        results.put(("error", index, repr(exc)))
    else:
        results.put(("ok", index, ""))


def _spawn_context() -> multiprocessing.context.BaseContext:
    # Spawn avoids inheriting the parent's Python state and exercises the same
    # file-system lock boundary on POSIX and Windows.
    return multiprocessing.get_context("spawn")


def test_fetch_head_lock_descriptor_is_explicitly_non_inheritable(tmp_path: Path) -> None:
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()

    fd, lock_path = _acquire_fetch_head_lock(pygit_dir)
    try:
        assert os.get_inheritable(fd) is False
        assert lock_path == pygit_dir / "FETCH_HEAD.lock"
    finally:
        os.close(fd)
        lock_path.unlink()


def test_foreign_process_lock_is_not_stolen_or_deleted(tmp_path: Path) -> None:
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    ctx = _spawn_context()
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(
        target=_hold_fetch_head_lock,
        args=(str(pygit_dir), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(15), "lock holder did not acquire FETCH_HEAD.lock"
        lock_path = pygit_dir / "FETCH_HEAD.lock"
        assert lock_path.read_bytes() == b"foreign-writer"

        with pytest.raises(FileExistsError):
            write_fetch_head_durable(
                pygit_dir,
                {"refs/heads/main": "1" * 64},
                source="origin",
                mergeable=("refs/heads/main",),
            )

        # The losing writer must not unlink or replace another process's lock.
        assert lock_path.read_bytes() == b"foreign-writer"
        assert not (pygit_dir / "FETCH_HEAD").exists()
    finally:
        release.set()
        holder.join(15)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
    assert holder.exitcode == 0
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()


def test_multiprocess_burst_never_publishes_torn_fetch_head(tmp_path: Path) -> None:
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    ctx = _spawn_context()
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(target=_burst_writer, args=(str(pygit_dir), start, results, index))
        for index in range(8)
    ]
    for process in processes:
        process.start()
    start.set()

    outcomes = []
    for _ in processes:
        outcomes.append(results.get(timeout=20))
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert all(process.exitcode == 0 for process in processes)
    assert not [item for item in outcomes if item[0] == "error"]
    successful = {item[1] for item in outcomes if item[0] == "ok"}
    assert successful, outcomes

    # Writers may serialize if a later process reaches O_EXCL after an earlier
    # rename. That is valid lockfile behavior. The important invariant is that
    # the live file is exactly one complete writer payload, never an interleave.
    final = (pygit_dir / "FETCH_HEAD").read_bytes()
    valid_payloads = {
        _render_fetch_head(
            {f"refs/heads/topic-{index}": f"{index + 1:064x}"},
            source="origin",
            mergeable=(f"refs/heads/topic-{index}",),
        )
        for index in successful
    }
    assert final in valid_payloads
    assert not (pygit_dir / "FETCH_HEAD.lock").exists()
