from __future__ import annotations

from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_refs as phase348
from pygit import Repository
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.refs import ZERO_SHA


NATIVE = "a" * 40


def _commit(repo: Repository) -> str:
    target = repo.worktree / "tracked.txt"
    target.write_text("phase348\n", encoding="utf-8")
    repo.add(["tracked.txt"])
    return repo.commit("phase348 durable ref")


def _certificate(tip: str) -> PackfileUriRootCertificate:
    return PackfileUriRootCertificate({NATIVE: tip}, {NATIVE: b"commit"})


def _publication(old: str = ZERO_SHA) -> dict[str, PackfileUriRefPublication]:
    return {
        "refs/remotes/origin/main": PackfileUriRefPublication(NATIVE, old),
    }


def _relative(repo: Repository, path: Path) -> str:
    return path.resolve().relative_to(repo.pygit_dir.resolve()).as_posix()


def test_durable_publication_fsyncs_files_before_unlock_and_directories_after(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    lock = repo.pygit_dir / "refs" / "remotes" / "origin" / "main.lock"
    events: list[tuple[str, str]] = []

    def record_file(path: Path) -> None:
        assert lock.exists(), "target ref lock must cover file durability"
        events.append(("file", _relative(repo, path)))

    def record_directory(path: Path) -> None:
        assert not lock.exists(), "directory fence must persist canonical lock removal"
        events.append(("dir", _relative(repo, path)))

    monkeypatch.setattr(phase348, "_fsync_file", record_file)
    monkeypatch.setattr(phase348, "_fsync_directory", record_directory)

    result = phase348.publish_packfile_uri_refs_durable(
        repo,
        _certificate(tip),
        _publication(),
    )

    assert result == {"refs/remotes/origin/main": tip}
    assert repo.refs.get_remote("origin", "main") == tip
    assert not lock.exists()
    assert events[:2] == [
        ("file", "refs/remotes/origin/main"),
        ("file", "logs/refs/remotes/origin/main"),
    ]
    directory_events = [value for kind, value in events if kind == "dir"]
    assert "refs/remotes/origin" in directory_events
    assert "logs/refs/remotes/origin" in directory_events
    assert "refs" in directory_events
    assert "logs" in directory_events
    assert "." in directory_events
    assert all(kind == "file" for kind, _ in events[:2])
    assert all(kind == "dir" for kind, _ in events[2:])


def test_file_fsync_failure_is_reported_after_visible_ref_and_releases_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    lock = repo.pygit_dir / "refs" / "remotes" / "origin" / "main.lock"
    directory_calls: list[Path] = []

    def fail_file(path: Path) -> None:
        assert lock.exists()
        raise OSError("injected ref fsync failure")

    monkeypatch.setattr(phase348, "_fsync_file", fail_file)
    monkeypatch.setattr(
        phase348,
        "_fsync_directory",
        lambda path: directory_calls.append(path),
    )

    with pytest.raises(OSError, match="injected ref fsync failure"):
        phase348.publish_packfile_uri_refs_durable(
            repo,
            _certificate(tip),
            _publication(),
        )

    # The generic ref transaction may already be visible. Durability failure is
    # deliberately propagated rather than pretending the transaction succeeded.
    assert repo.refs.get_remote("origin", "main") == tip
    assert not lock.exists()
    assert directory_calls == []


def test_directory_fsync_failure_happens_after_lock_release_and_propagates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    lock = repo.pygit_dir / "refs" / "remotes" / "origin" / "main.lock"
    seen_directories: list[str] = []

    monkeypatch.setattr(phase348, "_fsync_file", lambda path: None)

    def fail_directory(path: Path) -> None:
        assert not lock.exists()
        seen_directories.append(_relative(repo, path))
        raise OSError("injected ref directory fsync failure")

    monkeypatch.setattr(phase348, "_fsync_directory", fail_directory)

    with pytest.raises(OSError, match="injected ref directory fsync failure"):
        phase348.publish_packfile_uri_refs_durable(
            repo,
            _certificate(tip),
            _publication(),
        )

    assert seen_directories
    assert repo.refs.get_remote("origin", "main") == tip
    assert not lock.exists()


def test_up_to_date_publication_fsyncs_ref_without_inventing_reflog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    repo.refs.set_remote("origin", "main", tip)
    log = repo.pygit_dir / "logs" / "refs" / "remotes" / "origin" / "main"
    assert not log.exists()
    files: list[str] = []

    monkeypatch.setattr(
        phase348,
        "_fsync_file",
        lambda path: files.append(_relative(repo, path)),
    )
    monkeypatch.setattr(phase348, "_fsync_directory", lambda path: None)

    result = phase348.publish_packfile_uri_refs_durable(
        repo,
        _certificate(tip),
        _publication(tip),
    )

    assert result == {"refs/remotes/origin/main": tip}
    assert files == ["refs/remotes/origin/main"]
    assert not log.exists()


def test_multi_ref_durability_deduplicates_directory_fences(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    native_b = "b" * 40
    certificate = PackfileUriRootCertificate(
        {NATIVE: tip, native_b: tip},
        {NATIVE: b"commit", native_b: b"commit"},
    )
    publications = {
        "refs/remotes/origin/main": PackfileUriRefPublication(NATIVE, ZERO_SHA),
        "refs/remotes/origin/topic/x": PackfileUriRefPublication(native_b, ZERO_SHA),
    }
    directories: list[str] = []

    monkeypatch.setattr(phase348, "_fsync_file", lambda path: None)
    monkeypatch.setattr(
        phase348,
        "_fsync_directory",
        lambda path: directories.append(_relative(repo, path)),
    )

    result = phase348.publish_packfile_uri_refs_durable(
        repo,
        certificate,
        publications,
    )

    assert result == {
        "refs/remotes/origin/main": tip,
        "refs/remotes/origin/topic/x": tip,
    }
    assert len(directories) == len(set(directories))
    assert directories[-1] == "."
    assert not (repo.pygit_dir / "refs" / "remotes" / "origin" / "main.lock").exists()
    assert not (
        repo.pygit_dir / "refs" / "remotes" / "origin" / "topic" / "x.lock"
    ).exists()


def test_sha_domains_remain_native_sha1_to_content_sha256(tmp_path: Path, monkeypatch) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    assert len(NATIVE) == 40
    assert len(tip) == 64
    assert NATIVE != tip[:40]

    monkeypatch.setattr(phase348, "_fsync_file", lambda path: None)
    monkeypatch.setattr(phase348, "_fsync_directory", lambda path: None)

    result = phase348.publish_packfile_uri_refs_durable(
        repo,
        _certificate(tip),
        _publication(),
    )

    assert result["refs/remotes/origin/main"] == tip
    assert len(result["refs/remotes/origin/main"]) == 64
