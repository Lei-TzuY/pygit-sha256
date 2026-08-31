from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase348
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.refs import ZERO_SHA
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)


def _known_only_fixture(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    local = "2" * 64
    staged = StagedPackfileUriImport({}, ())
    incremental = PackfileUriIncrementalState((native,), {native: local}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    certificate = object()

    monkeypatch.setattr(phase348, "_download_optional_packfile_uris", lambda *a, **k: EMPTY_BATCH)
    monkeypatch.setattr(phase348, "stage_packfile_uri_import", lambda *a, **k: staged)
    monkeypatch.setattr(phase348, "certify_packfile_uri_roots", lambda *a, **k: certificate)

    return repo, native, local, incremental, publication, certificate


def _hold_state_guard(
    pygit_dir: str,
    ready: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    lock = phase348._acquire_fetch_head_state_guard(Path(pygit_dir))
    try:
        ready.set()
        if not release.wait(15):
            raise RuntimeError("timed out waiting to release FETCH_HEAD state guard")
    finally:
        phase348._release_fetch_head_state_guard(lock)


def _spawn_context() -> multiprocessing.context.BaseContext:
    return multiprocessing.get_context("spawn")


def test_state_guard_uses_distinct_fail_closed_lockfile(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    lock = phase348._acquire_fetch_head_state_guard(repo.pygit_dir)
    try:
        assert lock == repo.pygit_dir / "FETCH_HEAD.state.lock"
        assert lock.read_bytes() == b"packfile-uri FETCH_HEAD state guard\n"
        assert not (repo.pygit_dir / "FETCH_HEAD.lock").exists()

        with pytest.raises(RuntimeError, match="FETCH_HEAD state.*already exists"):
            phase348._acquire_fetch_head_state_guard(repo.pygit_dir)

        assert lock.read_bytes() == b"packfile-uri FETCH_HEAD state guard\n"
    finally:
        phase348._release_fetch_head_state_guard(lock)
    assert not lock.exists()


def test_early_clear_is_serialized_only_for_the_durable_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    events: list[str] = []
    fake_lock = repo.pygit_dir / "FETCH_HEAD.state.lock"

    monkeypatch.setattr(
        phase348,
        "_acquire_fetch_head_state_guard",
        lambda pygit_dir: events.append("state-acquire") or fake_lock,
    )
    monkeypatch.setattr(
        phase348,
        "write_fetch_head",
        lambda pygit_dir, refs, **kwargs: events.append(
            f"write-empty:{refs!r}:{kwargs['source']}"
        ),
    )
    monkeypatch.setattr(
        phase348,
        "_release_fetch_head_state_guard",
        lambda lock: events.append("state-release"),
    )

    phase348._clear_fetch_head_for_fetch(repo, source="https://example.test/repo.git")

    assert events == [
        "state-acquire",
        "write-empty:{}:https://example.test/repo.git",
        "state-release",
    ]


def test_final_publication_holds_state_guard_around_repository_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, native, local, incremental, publication, certificate = _known_only_fixture(
        tmp_path, monkeypatch
    )
    events: list[str] = []
    state_lock = repo.pygit_dir / "FETCH_HEAD.state.lock"
    repo_locks = [repo.pygit_dir / "HEAD.lock"]

    monkeypatch.setattr(
        phase348,
        "_acquire_fetch_head_state_guard",
        lambda pygit_dir: events.append("state-acquire") or state_lock,
    )
    monkeypatch.setattr(
        phase348,
        "_release_fetch_head_state_guard",
        lambda lock: events.append("state-release"),
    )
    monkeypatch.setattr(
        phase348,
        "_acquire_publication_guard_locks",
        lambda repo_arg: events.append("repo-acquire") or repo_locks,
    )
    monkeypatch.setattr(
        phase348,
        "_assert_publication_state_unchanged",
        lambda *a, **k: events.append("assert-state"),
    )
    monkeypatch.setattr(
        phase348,
        "publish_packfile_uri_refs",
        lambda *a, **k: events.append("refs")
        or {"refs/remotes/origin/main": local},
    )
    monkeypatch.setattr(
        phase348,
        "_release_publication_guard_locks",
        lambda locks: events.append("repo-release"),
    )

    result = phase348.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
        before_ref_publication=lambda seen: events.append("fetch-head")
        if seen is certificate
        else (_ for _ in ()).throw(AssertionError("wrong certificate")),
    )

    assert result.published_refs == {"refs/remotes/origin/main": local}
    assert events == [
        "state-acquire",
        "repo-acquire",
        "assert-state",
        "fetch-head",
        "refs",
        "repo-release",
        "state-release",
    ]


def test_concurrent_early_clear_cannot_erase_populated_final_fetch_head(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, native, local, incremental, publication, _ = _known_only_fixture(
        tmp_path, monkeypatch
    )
    source_ref = "refs/heads/main"
    source = "https://example.test/repo.git"
    attempted_clear = []

    monkeypatch.setattr(phase348, "_acquire_publication_guard_locks", lambda repo_arg: [])
    monkeypatch.setattr(phase348, "_assert_publication_state_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(
        phase348,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/remotes/origin/main": local},
    )
    monkeypatch.setattr(phase348, "_release_publication_guard_locks", lambda locks: None)

    def publish_then_race_clear(certificate) -> None:
        phase348.write_fetch_head(
            repo.pygit_dir,
            {source_ref: local},
            source=source,
            mergeable=(source_ref,),
        )
        populated = (repo.pygit_dir / "FETCH_HEAD").read_bytes()
        assert populated.startswith(local.encode("ascii") + b"\t\t")
        assert (repo.pygit_dir / "FETCH_HEAD.state.lock").exists()

        with pytest.raises(RuntimeError, match="FETCH_HEAD state.*already exists"):
            phase348._clear_fetch_head_for_fetch(repo, source="https://racer.invalid/repo.git")
        attempted_clear.append(True)
        assert (repo.pygit_dir / "FETCH_HEAD").read_bytes() == populated

    result = phase348.execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
        before_ref_publication=publish_then_race_clear,
    )

    assert attempted_clear == [True]
    assert result.published_refs == {"refs/remotes/origin/main": local}
    final = (repo.pygit_dir / "FETCH_HEAD").read_bytes()
    assert final.startswith(local.encode("ascii") + b"\t\t")
    assert not (repo.pygit_dir / "FETCH_HEAD.state.lock").exists()
    assert not (repo.pygit_dir / "FETCH_HEAD.lock").exists()


def test_publication_guard_contention_releases_fetch_head_state_guard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, native, _, incremental, publication, _ = _known_only_fixture(tmp_path, monkeypatch)

    monkeypatch.setattr(
        phase348,
        "_acquire_publication_guard_locks",
        lambda repo_arg: (_ for _ in ()).throw(RuntimeError("repository publication busy")),
    )

    with pytest.raises(RuntimeError, match="repository publication busy"):
        phase348.execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            {native: b"commit"},
            {"refs/remotes/origin/main": publication},
            incremental,
            before_ref_publication=lambda certificate: pytest.fail(
                "FETCH_HEAD hook must not run without repository guards"
            ),
        )

    assert not (repo.pygit_dir / "FETCH_HEAD.state.lock").exists()


def test_failed_early_clear_releases_fetch_head_state_guard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    monkeypatch.setattr(
        phase348,
        "write_fetch_head",
        lambda *a, **k: (_ for _ in ()).throw(OSError("injected FETCH_HEAD failure")),
    )

    with pytest.raises(OSError, match="injected FETCH_HEAD failure"):
        phase348._clear_fetch_head_for_fetch(repo, source="origin")

    assert not (repo.pygit_dir / "FETCH_HEAD.state.lock").exists()


def test_foreign_process_state_guard_blocks_early_clear_without_mutation(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    fetch_head = repo.pygit_dir / "FETCH_HEAD"
    fetch_head.write_bytes(b"previous complete FETCH_HEAD\n")

    ctx = _spawn_context()
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(
        target=_hold_state_guard,
        args=(str(repo.pygit_dir), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(15), "state-guard holder did not acquire its lock"
        state_lock = repo.pygit_dir / "FETCH_HEAD.state.lock"
        assert state_lock.read_bytes() == b"packfile-uri FETCH_HEAD state guard\n"

        with pytest.raises(RuntimeError, match="FETCH_HEAD state.*already exists"):
            phase348._clear_fetch_head_for_fetch(repo, source="origin")

        assert fetch_head.read_bytes() == b"previous complete FETCH_HEAD\n"
        assert state_lock.exists()
    finally:
        release.set()
        holder.join(15)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)

    assert holder.exitcode == 0
    assert not (repo.pygit_dir / "FETCH_HEAD.state.lock").exists()
