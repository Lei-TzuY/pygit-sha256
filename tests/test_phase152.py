"""Phase 152 tests: staged rename detection and porcelain type-2 records."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.status_cli import status_records
from pygit.status_renames import detect_staged_renames, parse_similarity_threshold


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


def _base_repo(tmp_path: Path, text: str = "alpha\nbeta\ngamma\n") -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "old.txt", text)
    repo.add(["old.txt"])
    repo.commit("base")
    return repo


def _stage_rename(repo: Repository, text: str | None = None) -> None:
    old_text = (repo.worktree / "old.txt").read_text(encoding="utf-8")
    repo.rm("old.txt")
    _write(repo, "new.txt", old_text if text is None else text)
    repo.add(["new.txt"])


def test_parse_find_renames_threshold_matches_common_git_spellings() -> None:
    assert parse_similarity_threshold(None) == 50
    assert parse_similarity_threshold("") == 50
    assert parse_similarity_threshold("90%") == 90
    assert parse_similarity_threshold("90") == 90
    assert parse_similarity_threshold("5") == 50
    assert parse_similarity_threshold("05") == 5
    assert parse_similarity_threshold("0.75") == 75


def test_exact_staged_rename_is_detected_as_r100(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_rename(repo)

    matches = detect_staged_renames(repo)
    assert matches == [matches[0]]
    assert matches[0].source == "old.txt"
    assert matches[0].target == "new.txt"
    assert matches[0].score == 100

    records = status_records(repo)
    assert records == [
        type(records[0])(
            path="new.txt",
            code="R ",
            orig_path="old.txt",
            score=100,
        )
    ]


def test_short_and_porcelain_v1_render_human_rename_arrow(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_rename(repo)

    short = _run(repo, "status", "-s")
    porcelain = _run(repo, "status", "--porcelain=v1")

    assert short.returncode == 0, short.stderr
    assert porcelain.returncode == 0, porcelain.stderr
    assert short.stdout == "R  old.txt -> new.txt\n"
    assert porcelain.stdout == short.stdout


def test_porcelain_v1_z_uses_target_then_source_nul_order(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_rename(repo)

    result = _run(repo, "status", "--porcelain=v1", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "R  new.txt\0old.txt\0"


def test_porcelain_v2_emits_type2_r100_metadata(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head is not None
    head_oid, head_mode = repo._commit_tree_entries(head)["old.txt"]
    _stage_rename(repo)
    index_entry = repo.index.get("new.txt")
    assert index_entry is not None

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "2 R. N... "
        f"{head_mode} {index_entry.mode} 100644 {head_oid} {index_entry.sha} "
        "R100 new.txt\told.txt\n"
    )


def test_porcelain_v2_z_separates_target_and_source_with_nul(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_rename(repo)

    result = _run(repo, "status", "--porcelain=v2", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("2 R. N... ")
    assert result.stdout.endswith(" R100 new.txt\0old.txt\0")


def test_target_worktree_edit_preserves_rename_with_rm_xy(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_rename(repo)
    _write(repo, "new.txt", "worktree changed after staging\n")

    short = _run(repo, "status", "-s")
    v2 = _run(repo, "status", "--porcelain=v2")

    assert short.stdout == "RM old.txt -> new.txt\n"
    assert v2.stdout.startswith("2 RM N... ")
    assert " R100 new.txt\told.txt\n" in v2.stdout


def test_no_renames_restores_delete_add_records(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_rename(repo)

    v1 = _run(repo, "status", "--porcelain=v1", "--no-renames")
    v2 = _run(repo, "status", "--porcelain=v2", "--no-renames")

    assert v1.returncode == 0, v1.stderr
    assert "A  new.txt\n" in v1.stdout
    assert "D  old.txt\n" in v1.stdout
    assert "R" not in v1.stdout
    assert "2 R" not in v2.stdout
    assert "1 A." in v2.stdout
    assert "1 D." in v2.stdout


def test_find_renames_threshold_controls_modified_pair(tmp_path: Path) -> None:
    base = "abcdefghij\n" * 20
    repo = _base_repo(tmp_path, base)
    changed = base.replace("abcdefghij", "abcXefghij", 4)
    _stage_rename(repo, changed)

    matches = detect_staged_renames(repo, threshold=50)
    assert len(matches) == 1
    assert 50 <= matches[0].score < 100

    strict = _run(repo, "status", "--porcelain=v2", "--find-renames=100%")
    permissive = _run(repo, "status", "--porcelain=v2", "--find-renames=50%")

    assert "2 R" not in strict.stdout
    assert "1 A." in strict.stdout and "1 D." in strict.stdout
    assert "2 R" in permissive.stdout
    assert f" R{matches[0].score} new.txt\told.txt\n" in permissive.stdout


def test_unstaged_filesystem_move_is_not_synthesized_as_staged_rename(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    (repo.worktree / "old.txt").rename(repo.worktree / "new.txt")

    matches = detect_staged_renames(repo)
    result = _run(repo, "status", "--porcelain=v1")

    assert matches == []
    assert " D old.txt\n" in result.stdout
    assert "?? new.txt\n" in result.stdout
    assert "R" not in result.stdout


def test_status_help_advertises_rename_controls(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    result = _run(repo, "status", "--help")
    assert result.returncode == 0
    assert "--renames" in result.stdout
    assert "--no-renames" in result.stdout
    assert "--find-renames" in result.stdout
