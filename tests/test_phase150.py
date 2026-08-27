"""Phase 150 tests: index-derived status conflict codes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.index import IndexEntry
from pygit.objects import BlobObject
from pygit.status_cli import status_records, unmerged_status


def _run(repo: Repository, *args: str):
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


def _install_unmerged(repo: Repository, path: str, stages: tuple[int, ...]) -> None:
    for stage in stages:
        data = f"{path}:stage-{stage}\n".encode()
        oid = repo.store.write(BlobObject(data))
        repo.index.set_entry(
            IndexEntry(path, oid, "100644", len(data), 0.0, stage),
        )
    # Keep a worktree file around to prove conflict paths are not re-reported as
    # untracked merely because stage zero is intentionally absent.
    _write(repo, path, "worktree conflict\n")


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

    result = repo.merge("feature")
    assert result["status"] == "conflicts"
    return repo


def test_all_seven_unmerged_stage_combinations_have_git_codes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    cases = {
        "aa.txt": ((2, 3), "AA"),
        "au.txt": ((2,), "AU"),
        "dd.txt": ((1,), "DD"),
        "du.txt": ((1, 3), "DU"),
        "ua.txt": ((3,), "UA"),
        "ud.txt": ((1, 2), "UD"),
        "uu.txt": ((1, 2, 3), "UU"),
    }
    for path, (stages, _code) in cases.items():
        _install_unmerged(repo, path, stages)
    repo.index.save()

    actual = {record.path: (record.stages, record.code) for record in unmerged_status(repo)}
    expected = {path: (stages, code) for path, (stages, code) in cases.items()}
    assert actual == expected

    records = {record.path: record.code for record in status_records(repo)}
    assert records == {path: code for path, (_stages, code) in cases.items()}


def test_porcelain_and_short_report_real_merge_as_single_uu_record(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)

    porcelain = _run(repo, "status", "--porcelain")
    assert porcelain.returncode == 0, porcelain.stderr
    assert porcelain.stdout == "UU conflict.txt\n"

    short = _run(repo, "status", "-s")
    assert short.returncode == 0, short.stderr
    assert short.stdout == "UU conflict.txt\n"
    assert "?? conflict.txt" not in short.stdout
    assert "D  conflict.txt" not in short.stdout


def test_porcelain_v1_spelling_is_accepted(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)
    result = _run(repo, "status", "--porcelain=v1")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "UU conflict.txt\n"


def test_modify_delete_conflict_reports_deleted_by_them(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "gone.txt", "base\n")
    repo.add(["gone.txt"])
    repo.commit("base")

    repo.branch("delete-side")
    repo.rm("gone.txt")
    repo.commit("delete theirs")

    repo.checkout("main")
    _write(repo, "gone.txt", "ours changed\n")
    repo.add(["gone.txt"])
    repo.commit("modify ours")
    assert repo.merge("delete-side")["status"] == "conflicts"

    porcelain = _run(repo, "status", "--porcelain")
    assert porcelain.returncode == 0, porcelain.stderr
    assert porcelain.stdout == "UD gone.txt\n"

    full = _run(repo, "status")
    assert full.returncode == 0, full.stderr
    assert "Unmerged paths:" in full.stdout
    assert "deleted by them:\tgone.txt" in full.stdout
    assert "both modified:\tgone.txt" not in full.stdout


def test_full_status_renders_every_conflict_label(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    cases = {
        "aa.txt": ((2, 3), "both added"),
        "au.txt": ((2,), "added by us"),
        "dd.txt": ((1,), "both deleted"),
        "du.txt": ((1, 3), "deleted by us"),
        "ua.txt": ((3,), "added by them"),
        "ud.txt": ((1, 2), "deleted by them"),
        "uu.txt": ((1, 2, 3), "both modified"),
    }
    for path, (stages, _label) in cases.items():
        _install_unmerged(repo, path, stages)
    repo.index.save()

    result = _run(repo, "status")
    assert result.returncode == 0, result.stderr
    for path, (_stages, label) in cases.items():
        assert f"{label}:\t{path}" in result.stdout


def test_git_add_resolution_removes_unmerged_code(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _install_unmerged(repo, "conflict.txt", (1, 2, 3))
    repo.index.save()
    assert {record.code for record in status_records(repo)} == {"UU"}

    _write(repo, "conflict.txt", "resolved\n")
    repo.add(["conflict.txt"])

    assert not repo.index.has_unmerged("conflict.txt")
    assert all(record.code != "UU" for record in status_records(repo))


def test_porcelain_only_shows_ignored_paths_when_requested(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, ".gitignore", "ignored.txt\n")
    _write(repo, "ignored.txt", "ignored\n")

    normal = _run(repo, "status", "--porcelain")
    assert normal.returncode == 0, normal.stderr
    assert "ignored.txt" not in normal.stdout
    assert "?? .gitignore" in normal.stdout

    requested = _run(repo, "status", "--porcelain", "--ignored")
    assert requested.returncode == 0, requested.stderr
    assert "!! ignored.txt" in requested.stdout


def test_short_branch_header_reads_nested_upstream_counts(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "a.txt", "base\n")
    repo.add(["a.txt"])
    base = repo.commit("base")
    repo.refs.set_remote("origin", "main", base)

    _write(repo, "a.txt", "ahead\n")
    repo.add(["a.txt"])
    repo.commit("ahead")

    result = _run(repo, "status", "-sb")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "## main...origin/main [ahead 1]"
    assert "{'upstream'" not in result.stdout
