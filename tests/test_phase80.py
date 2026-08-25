"""Phase 80 tests: exact, tri-state ``show-ref --exists`` queries."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, ref_exists
from pygit.packed_refs import PackedRef, write_packed_refs


FAKE_OID = "a" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "show-ref", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_api_reports_loose_ref_without_resolving_object(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo.pygit_dir / "refs" / "heads" / "broken"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_OID, encoding="utf-8")

    assert ref_exists(repo, "refs/heads/broken") is True
    assert not repo.store.exists(FAKE_OID)


def test_api_counts_dangling_symbolic_ref_as_existing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo.pygit_dir / "refs" / "heads" / "alias"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ref: refs/heads/missing", encoding="utf-8")

    assert ref_exists(repo, "refs/heads/alias") is True
    assert repo.refs.resolve("refs/heads/alias") is None


def test_api_finds_packed_only_ref_even_when_object_is_missing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    write_packed_refs(repo.pygit_dir, [PackedRef(FAKE_OID, "refs/tags/packed")])

    assert ref_exists(repo, "refs/tags/packed") is True
    assert not (repo.pygit_dir / "refs" / "tags" / "packed").exists()
    assert not repo.store.exists(FAKE_OID)


def test_api_rejects_non_exact_or_unsafe_names(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="exact ref"):
        ref_exists(repo, "main")
    with pytest.raises(ValueError):
        ref_exists(repo, "refs/heads/../../outside")


def test_cli_returns_zero_for_existing_and_two_for_missing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo.pygit_dir / "refs" / "heads" / "topic"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE_OID, encoding="utf-8")

    existing = _run(repo, "--exists", "refs/heads/topic")
    assert existing.returncode == 0
    assert existing.stdout == ""
    assert existing.stderr == ""

    missing = _run(repo, "--exists", "refs/heads/missing")
    assert missing.returncode == 2
    assert missing.stdout == ""
    assert missing.stderr == ""


def test_cli_dangling_symbolic_ref_is_still_existing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = repo.pygit_dir / "refs" / "heads" / "alias"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ref: refs/heads/nowhere", encoding="utf-8")

    result = _run(repo, "--exists", "refs/heads/alias")
    assert result.returncode == 0
    assert result.stdout == ""


def test_cli_storage_error_returns_one_not_missing_two(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.pygit_dir / "packed-refs").write_text(
        "not-an-oid refs/heads/bad\n",
        encoding="utf-8",
    )

    result = _run(repo, "--exists", "refs/heads/missing")
    assert result.returncode == 1
    assert "error:" in result.stderr
    assert "packed-refs" in result.stderr


def test_cli_exists_requires_one_ref_and_rejects_other_modes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    no_ref = _run(repo, "--exists")
    assert no_ref.returncode == 2
    assert "exactly one reference" in no_ref.stderr

    two_refs = _run(repo, "--exists", "refs/heads/a", "refs/heads/b")
    assert two_refs.returncode == 2
    assert "exactly one reference" in two_refs.stderr

    incompatible = _run(repo, "--exists", "--tags", "refs/tags/v1")
    assert incompatible.returncode == 2
    assert "cannot be combined" in incompatible.stderr

    invalid = _run(repo, "--exists", "main")
    assert invalid.returncode == 1
    assert "exact ref" in invalid.stderr
