"""Phase 151 tests: Git-style status porcelain v2 records."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository


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


def _conflicted_repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "conflict.txt", "base\n")
    repo.add(["conflict.txt"])
    repo.commit("base")
    repo.branch("feature")

    _write(repo, "conflict.txt", "theirs\n")
    repo.add(["conflict.txt"])
    repo.commit("theirs")

    repo.checkout("main")
    _write(repo, "conflict.txt", "ours\n")
    repo.add(["conflict.txt"])
    repo.commit("ours")
    assert repo.merge("feature")["status"] == "conflicts"
    return repo


def test_v2_staged_add_reports_modes_and_sha256_metadata(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "new.txt", "new\n")
    repo.add(["new.txt"])
    entry = repo.index.get("new.txt")
    assert entry is not None

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"1 A. N... 000000 100644 100644 {'0' * 64} {entry.sha} new.txt\n"
    )


def test_v2_staged_and_unstaged_xy_use_dot_for_unchanged(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "file.txt", "base\n")
    repo.add(["file.txt"])
    head = repo.commit("base")
    head_oid, head_mode = repo._commit_tree_entries(head)["file.txt"]

    _write(repo, "file.txt", "staged\n")
    repo.add(["file.txt"])
    staged = repo.index.get("file.txt")
    assert staged is not None
    staged_result = _run(repo, "status", "--porcelain=v2")
    assert staged_result.stdout == (
        f"1 M. N... {head_mode} {staged.mode} 100644 {head_oid} {staged.sha} file.txt\n"
    )

    _write(repo, "file.txt", "worktree\n")
    unstaged_result = _run(repo, "status", "--porcelain=v2")
    assert unstaged_result.stdout.startswith(
        f"1 MM N... {head_mode} {staged.mode} 100644 {head_oid} {staged.sha} file.txt"
    )


def test_v2_worktree_delete_uses_zero_worktree_mode(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "gone.txt", "base\n")
    repo.add(["gone.txt"])
    head = repo.commit("base")
    head_oid, mode = repo._commit_tree_entries(head)["gone.txt"]
    entry = repo.index.get("gone.txt")
    assert entry is not None
    (repo.worktree / "gone.txt").unlink()

    result = _run(repo, "status", "--porcelain=v2")

    assert result.stdout == (
        f"1 .D N... {mode} {entry.mode} 000000 {head_oid} {entry.sha} gone.txt\n"
    )


def test_v2_unmerged_record_exposes_all_stage_metadata(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)
    stages = [repo.index.get("conflict.txt", stage) for stage in (1, 2, 3)]
    assert all(entry is not None for entry in stages)

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    entries = [entry for entry in stages if entry is not None]
    assert result.stdout == (
        "u UU N... "
        f"{entries[0].mode} {entries[1].mode} {entries[2].mode} 100644 "
        f"{entries[0].sha} {entries[1].sha} {entries[2].sha} conflict.txt\n"
    )


def test_v2_branch_headers_cover_initial_and_upstream_counts(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    initial = _run(repo, "status", "--porcelain=v2", "--branch")
    assert initial.stdout.splitlines()[:2] == [
        "# branch.oid (initial)",
        "# branch.head main",
    ]

    _write(repo, "a.txt", "base\n")
    repo.add(["a.txt"])
    base = repo.commit("base")
    repo.refs.set_remote("origin", "main", base)
    _write(repo, "a.txt", "ahead\n")
    repo.add(["a.txt"])
    head = repo.commit("ahead")

    result = _run(repo, "status", "--porcelain=v2", "--branch")
    assert result.stdout.splitlines()[:4] == [
        f"# branch.oid {head}",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +1 -0",
    ]


def test_v2_untracked_ignored_and_path_quoting(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, ".gitignore", "ignored.txt\n")
    _write(repo, "ignored.txt", "ignored\n")
    _write(repo, "odd\nname.txt", "odd\n")

    result = _run(repo, "status", "--porcelain=v2", "--ignored")

    assert result.returncode == 0, result.stderr
    assert "? .gitignore\n" in result.stdout
    assert '? "odd\\nname.txt"\n' in result.stdout
    assert "! ignored.txt\n" in result.stdout


def test_v2_z_uses_nul_terminators_and_raw_paths(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "odd\nname.txt", "odd\n")

    result = _run(repo, "status", "--porcelain=v2", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "? odd\nname.txt\0"


def test_v1_z_reuses_existing_codes_with_nul_termination(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "new.txt", "new\n")

    result = _run(repo, "status", "--porcelain=v1", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "?? new.txt\0"


def test_status_help_advertises_porcelain_v2_and_z(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    result = _run(repo, "status", "--help")
    assert result.returncode == 0
    assert "v1,v2" in result.stdout
    assert "-z" in result.stdout
