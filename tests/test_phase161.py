"""Phase 161 tests: status.renameLimit and exhaustive similarity gating."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.status_renames import (
    configured_rename_limit,
    detect_staged_copies,
    detect_staged_renames,
    parse_rename_limit,
)


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _content(index: int, *, changed: bool = False) -> str:
    token = chr(ord("A") + index) * 24
    lines = [token for _ in range(18)]
    if changed:
        lines[7] = token[:-1] + "x"
    return "\n".join(lines) + "\n"


def _rename_repo(tmp_path: Path, *, count: int = 3, exact: bool = False) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    for i in range(count):
        _write(repo, f"old{i}.txt", _content(i))
    repo.add([f"old{i}.txt" for i in range(count)])
    repo.commit("base")

    for i in range(count):
        repo.rm(f"old{i}.txt")
        _write(repo, f"new{i}.txt", _content(i, changed=not exact))
    repo.add([f"new{i}.txt" for i in range(count)])
    return repo


def _copy_repo(tmp_path: Path, *, count: int = 3) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    for i in range(count):
        _write(repo, f"src{i}.txt", _content(i))
    repo.add([f"src{i}.txt" for i in range(count)])
    repo.commit("base")

    for i in range(count):
        changed = _content(i, changed=True)
        _write(repo, f"src{i}.txt", changed)
        _write(repo, f"copy{i}.txt", changed)
    repo.add(
        [f"src{i}.txt" for i in range(count)]
        + [f"copy{i}.txt" for i in range(count)]
    )
    repo.config_set("status", "renames", "copies")
    return repo


def test_parse_rename_limit_accepts_git_integer_units_and_unlimited_values() -> None:
    assert parse_rename_limit("1") == 1
    assert parse_rename_limit("0") == 0
    assert parse_rename_limit("-1") == -1
    assert parse_rename_limit("2k") == 2 * 1024
    assert parse_rename_limit("3M") == 3 * 1024 * 1024
    assert parse_rename_limit("1g") == 1024 * 1024 * 1024

    with pytest.raises(ValueError):
        parse_rename_limit("")
    with pytest.raises(ValueError):
        parse_rename_limit("12kb")
    with pytest.raises(ValueError):
        parse_rename_limit("many")


def test_configured_rename_limit_prefers_status_then_diff_then_default(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    assert configured_rename_limit(repo) == 1000

    repo.config_set("diff", "renameLimit", "7")
    assert configured_rename_limit(repo) == 7

    repo.config_set("status", "renameLimit", "3")
    assert configured_rename_limit(repo) == 3

    repo.config_unset("status", "renameLimit")
    assert configured_rename_limit(repo) == 7


def test_exact_renames_survive_a_low_limit(tmp_path: Path) -> None:
    repo = _rename_repo(tmp_path, exact=True)

    matches = detect_staged_renames(repo, limit=1)

    assert len(matches) == 3
    assert all(match.score == 100 for match in matches)
    assert [(m.source, m.target) for m in matches] == [
        ("old0.txt", "new0.txt"),
        ("old1.txt", "new1.txt"),
        ("old2.txt", "new2.txt"),
    ]


def test_similarity_rename_fallback_is_gated_by_limit(tmp_path: Path) -> None:
    repo = _rename_repo(tmp_path)

    assert detect_staged_renames(repo, limit=2) == []
    matches = detect_staged_renames(repo, limit=3)

    assert len(matches) == 3
    assert all(50 <= match.score < 100 for match in matches)
    assert [(m.source, m.target) for m in matches] == [
        ("old0.txt", "new0.txt"),
        ("old1.txt", "new1.txt"),
        ("old2.txt", "new2.txt"),
    ]


def test_zero_and_negative_limits_are_unlimited(tmp_path: Path) -> None:
    repo = _rename_repo(tmp_path)

    assert len(detect_staged_renames(repo, limit=0)) == 3
    assert len(detect_staged_renames(repo, limit=-1)) == 3


def test_status_rename_limit_controls_cli_similarity_detection(tmp_path: Path) -> None:
    repo = _rename_repo(tmp_path)
    repo.config_set("status", "renameLimit", "2")

    limited = _run(repo, "status", "--porcelain=v1")
    limited_find = _run(repo, "status", "--porcelain=v1", "--find-renames=50%")

    assert limited.returncode == 0, limited.stderr
    assert limited_find.returncode == 0, limited_find.stderr
    assert "R " not in limited.stdout
    assert "R " not in limited_find.stdout
    assert limited.stdout.count("D  old") == 3
    assert limited.stdout.count("A  new") == 3

    repo.config_set("status", "renameLimit", "3")
    enabled = _run(repo, "status", "--porcelain=v1")

    assert enabled.returncode == 0, enabled.stderr
    assert enabled.stdout.count("R  old") == 3
    assert "D  old" not in enabled.stdout
    assert "A  new" not in enabled.stdout


def test_diff_rename_limit_is_used_when_status_limit_is_unset(tmp_path: Path) -> None:
    repo = _rename_repo(tmp_path)
    repo.config_set("diff", "renameLimit", "2")

    limited = _run(repo, "status", "--porcelain=v2")
    assert limited.returncode == 0, limited.stderr
    assert "2 R" not in limited.stdout
    assert limited.stdout.count("1 D.") == 3
    assert limited.stdout.count("1 A.") == 3

    repo.config_set("status", "renameLimit", "3")
    enabled = _run(repo, "status", "--porcelain=v2")
    assert enabled.stdout.count("2 R.") == 3


def test_similarity_copy_fallback_is_gated_by_same_limit(tmp_path: Path) -> None:
    repo = _copy_repo(tmp_path)

    assert detect_staged_copies(repo, limit=2) == []
    matches = detect_staged_copies(repo, limit=3)

    assert len(matches) == 3
    assert all(50 <= match.score < 100 for match in matches)
    assert [(m.source, m.target) for m in matches] == [
        ("src0.txt", "copy0.txt"),
        ("src1.txt", "copy1.txt"),
        ("src2.txt", "copy2.txt"),
    ]

    repo.config_set("status", "renameLimit", "2")
    limited = _run(repo, "status", "--porcelain=v1")
    assert "C  " not in limited.stdout
    assert limited.stdout.count("A  copy") == 3
    assert limited.stdout.count("M  src") == 3

    repo.config_set("status", "renameLimit", "3")
    enabled = _run(repo, "status", "--porcelain=v1")
    assert enabled.stdout.count("C  src") == 3
    assert enabled.stdout.count("M  src") == 3


def test_exact_copy_preimages_survive_limit_before_exhaustive_fallback(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    original = "original preimage\n" * 8
    _write(repo, "src.txt", original)
    repo.add(["src.txt"])
    repo.commit("base")

    _write(repo, "src.txt", "new source contents\n" * 8)
    _write(repo, "copy1.txt", original)
    _write(repo, "copy2.txt", original)
    repo.add(["src.txt", "copy1.txt", "copy2.txt"])
    repo.config_set("status", "renames", "copies")
    repo.config_set("status", "renameLimit", "1")

    matches = detect_staged_copies(repo)
    assert [(m.source, m.target, m.score) for m in matches] == [
        ("src.txt", "copy1.txt", 100),
        ("src.txt", "copy2.txt", 100),
    ]

    v2 = _run(repo, "status", "--porcelain=v2")
    assert v2.returncode == 0, v2.stderr
    assert v2.stdout.count("2 C.") == 2
    assert " C100 copy1.txt\tsrc.txt\n" in v2.stdout
    assert " C100 copy2.txt\tsrc.txt\n" in v2.stdout
    assert "1 M." in v2.stdout and v2.stdout.endswith("src.txt\n")


def test_status_does_not_invent_diff_only_find_copies_harder_option(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    result = _run(repo, "status", "--find-copies-harder")

    assert result.returncode != 0
    assert "unrecognized arguments: --find-copies-harder" in result.stderr
