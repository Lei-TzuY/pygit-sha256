"""Phase 126 tests: checkout-index conflict-stage extraction."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, checkout_index
from pygit.checkout_index_cli import run_checkout_index
from pygit.index_plumbing import update_index
from pygit.objects import BlobObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _blob(repo: Repository, data: bytes) -> str:
    return repo.store.write(BlobObject(data))


def _three_stages(repo: Repository, path: str = "conflict.txt") -> tuple[str, str, str]:
    base = _blob(repo, b"base\n")
    ours = _blob(repo, b"ours\n")
    theirs = _blob(repo, b"theirs\n")
    update_index(
        repo,
        index_info=[
            f"100644 {base} 1\t{path}",
            f"100644 {ours} 2\t{path}",
            f"100644 {theirs} 3\t{path}",
        ],
    )
    return base, ours, theirs


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_checkout_index_materializes_base_ours_and_theirs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)

    expected = {1: b"base\n", 2: b"ours\n", 3: b"theirs\n"}
    for stage, data in expected.items():
        written = checkout_index(
            repo,
            ["conflict.txt"],
            stage=stage,
            prefix=f"stage-{stage}",
        )
        target = repo.worktree / f"stage-{stage}" / "conflict.txt"
        assert written == [target]
        assert target.read_bytes() == data


def test_default_stage_zero_behavior_stays_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)
    normal = _blob(repo, b"normal\n")
    update_index(repo, cache_info=[("100644", normal, "normal.txt")])

    written = checkout_index(repo, all_entries=True, prefix="export")

    assert written == [repo.worktree / "export" / "normal.txt"]
    assert (repo.worktree / "export" / "normal.txt").read_bytes() == b"normal\n"
    assert not (repo.worktree / "export" / "conflict.txt").exists()


def test_all_entries_filters_to_requested_conflict_stage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo, "a.txt")
    base_only = _blob(repo, b"base-only\n")
    update_index(repo, index_info=[f"100644 {base_only} 1\tb.txt"])

    ours = checkout_index(repo, all_entries=True, stage=2, prefix="ours")
    assert ours == [repo.worktree / "ours" / "a.txt"]
    assert (repo.worktree / "ours" / "a.txt").read_bytes() == b"ours\n"
    assert not (repo.worktree / "ours" / "b.txt").exists()

    bases = checkout_index(repo, all_entries=True, stage=1, prefix="base")
    assert bases == [
        repo.worktree / "base" / "a.txt",
        repo.worktree / "base" / "b.txt",
    ]
    assert (repo.worktree / "base" / "b.txt").read_bytes() == b"base-only\n"


def test_missing_requested_stage_is_a_pathspec_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _blob(repo, b"base\n")
    update_index(repo, index_info=[f"100644 {base} 1\tconflict.txt"])

    with pytest.raises(KeyError, match="stage-2"):
        checkout_index(repo, ["conflict.txt"], stage=2)


def test_checkout_does_not_mutate_multistage_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, ours, theirs = _three_stages(repo)
    before = (repo.pygit_dir / "index").read_bytes()

    checkout_index(repo, ["conflict.txt"], stage=2, prefix="inspect")

    assert (repo.pygit_dir / "index").read_bytes() == before
    assert repo.index.get("conflict.txt", 1).sha == base
    assert repo.index.get("conflict.txt", 2).sha == ours
    assert repo.index.get("conflict.txt", 3).sha == theirs
    assert repo.index.get("conflict.txt") is None


def test_python_cli_adapter_selects_conflict_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)
    monkeypatch.chdir(repo.worktree)

    assert run_checkout_index(
        ["--stage=2", "--prefix=ours", "conflict.txt"]
    ) == 0
    assert (repo.worktree / "ours" / "conflict.txt").read_bytes() == b"ours\n"


def test_installed_cli_selects_theirs_stage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)

    result = _run(
        repo,
        "checkout-index",
        "--stage=3",
        "--prefix=theirs",
        "conflict.txt",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert (repo.worktree / "theirs" / "conflict.txt").read_bytes() == b"theirs\n"


def test_invalid_stage_is_rejected_by_python_api(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="stage must be 0, 1, 2, or 3"):
        checkout_index(repo, ["anything"], stage=4)
