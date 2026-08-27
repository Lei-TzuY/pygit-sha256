"""Phase 160 tests: staged copy detection and status.renames policy."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.status_cli import configured_rename_mode, status_records
from pygit.status_renames import detect_staged_copies


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


def _base_repo(tmp_path: Path, text: str = "abcdefghij\n" * 20) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "old.txt", text)
    repo.add(["old.txt"])
    repo.commit("base")
    return repo


def _stage_modified_copy(repo: Repository) -> None:
    base = (repo.worktree / "old.txt").read_text(encoding="utf-8")
    changed = base.replace("abcdefghij", "abcXefghij", 4)
    _write(repo, "old.txt", changed)
    _write(repo, "copy.txt", changed)
    repo.add(["old.txt", "copy.txt"])


def test_configured_rename_mode_status_then_diff_fallback(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    assert configured_rename_mode(repo) == "renames"

    repo.config_set("diff", "renames", "copies")
    assert configured_rename_mode(repo) == "copies"

    repo.config_set("status", "renames", "false")
    assert configured_rename_mode(repo) == "none"

    repo.config_set("status", "renames", "copy")
    assert configured_rename_mode(repo) == "copies"

    repo.config_set("status", "renames", "true")
    assert configured_rename_mode(repo) == "renames"


def test_unmodified_source_is_not_a_normal_copy_candidate(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    text = (repo.worktree / "old.txt").read_text(encoding="utf-8")
    _write(repo, "copy.txt", text)
    repo.add(["copy.txt"])
    repo.config_set("status", "renames", "copies")

    assert detect_staged_copies(repo) == []
    result = _run(repo, "status", "--porcelain=v1")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "A  copy.txt\n"


def test_worktree_only_source_change_is_not_copy_source(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    base = (repo.worktree / "old.txt").read_text(encoding="utf-8")
    changed = base.replace("abcdefghij", "abcXefghij", 4)
    _write(repo, "old.txt", changed)
    _write(repo, "copy.txt", changed)
    repo.add(["copy.txt"])
    repo.config_set("status", "renames", "copies")

    assert detect_staged_copies(repo) == []
    result = _run(repo, "status", "--porcelain=v1")
    assert "A  copy.txt\n" in result.stdout
    assert " M old.txt\n" in result.stdout
    assert "C " not in result.stdout


def test_staged_modified_source_enables_copy_record(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)

    matches = detect_staged_copies(repo)
    assert len(matches) == 1
    assert matches[0].source == "old.txt"
    assert matches[0].target == "copy.txt"
    assert 50 <= matches[0].score < 100

    records = status_records(repo, copies=True)
    assert [(r.path, r.code, r.orig_path) for r in records] == [
        ("copy.txt", "C ", "old.txt"),
        ("old.txt", "M ", None),
    ]


def test_status_renames_copies_drives_long_short_and_v1(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")

    long_status = _run(repo, "status")
    short = _run(repo, "status", "-s")
    v1 = _run(repo, "status", "--porcelain=v1")

    assert long_status.returncode == 0, long_status.stderr
    assert "copied:\told.txt -> copy.txt" in long_status.stdout
    assert "modified:\told.txt" in long_status.stdout
    assert "new file:\tcopy.txt" not in long_status.stdout
    assert short.stdout == "C  old.txt -> copy.txt\nM  old.txt\n"
    assert v1.stdout == short.stdout


def test_porcelain_v1_z_uses_copy_target_then_source(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")

    result = _run(repo, "status", "--porcelain=v1", "-z")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("C  copy.txt\0old.txt\0")
    assert result.stdout.endswith("M  old.txt\0")


def test_porcelain_v2_emits_c_score_and_keeps_source_record(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head is not None
    head_oid, head_mode = repo._commit_tree_entries(head)["old.txt"]
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")
    target = repo.index.get("copy.txt")
    source = repo.index.get("old.txt")
    assert target is not None and source is not None
    match = detect_staged_copies(repo)[0]

    result = _run(repo, "status", "--porcelain=v2")

    assert result.returncode == 0, result.stderr
    copy_line = next(line for line in result.stdout.splitlines() if line.startswith("2 C."))
    assert copy_line == (
        "2 C. N... "
        f"{head_mode} {target.mode} 100644 {head_oid} {target.sha} "
        f"C{match.score} copy.txt\told.txt"
    )
    assert any(line.startswith("1 M. ") and line.endswith(" old.txt") for line in result.stdout.splitlines())


def test_porcelain_v2_z_frames_copy_source_with_nul(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")
    match = detect_staged_copies(repo)[0]

    result = _run(repo, "status", "--porcelain=v2", "-z")

    assert result.returncode == 0, result.stderr
    assert f" C{match.score} copy.txt\0old.txt\0" in result.stdout
    assert "1 M. " in result.stdout


def test_copy_target_worktree_edit_preserves_cm_xy(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")
    _write(repo, "copy.txt", "changed after staging\n")

    short = _run(repo, "status", "-s")
    v2 = _run(repo, "status", "--porcelain=v2")

    assert short.stdout.startswith("CM old.txt -> copy.txt\n")
    assert "2 CM N... " in v2.stdout


def test_one_changed_source_can_feed_multiple_copy_targets(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    base = (repo.worktree / "old.txt").read_text(encoding="utf-8")
    changed = base.replace("abcdefghij", "abcXefghij", 4)
    _write(repo, "old.txt", changed)
    _write(repo, "copy-a.txt", changed)
    _write(repo, "copy-b.txt", changed)
    repo.add(["old.txt", "copy-a.txt", "copy-b.txt"])
    repo.config_set("status", "renames", "copies")

    matches = detect_staged_copies(repo)
    assert [(m.source, m.target) for m in matches] == [
        ("old.txt", "copy-a.txt"),
        ("old.txt", "copy-b.txt"),
    ]
    result = _run(repo, "status", "-s")
    assert result.stdout.count("C  old.txt ->") == 2
    assert "M  old.txt\n" in result.stdout


def test_explicit_rename_switches_override_configured_copies(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")

    renames_only = _run(repo, "status", "-s", "--renames")
    disabled = _run(repo, "status", "-s", "--no-renames")

    for result in (renames_only, disabled):
        assert "A  copy.txt\n" in result.stdout
        assert "M  old.txt\n" in result.stdout
        assert "C " not in result.stdout


def test_find_renames_preserves_copy_policy_and_controls_threshold(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")
    match = detect_staged_copies(repo)[0]

    strict = _run(repo, "status", "--porcelain=v2", "--find-renames=100%")
    permissive = _run(repo, "status", "--porcelain=v2", "--find-renames=50%")

    assert "2 C" not in strict.stdout
    assert "1 A." in strict.stdout
    assert "2 C." in permissive.stdout
    assert f" C{match.score} copy.txt\told.txt\n" in permissive.stdout


def test_find_renames_reenables_basic_renames_when_config_disabled(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    text = (repo.worktree / "old.txt").read_text(encoding="utf-8")
    repo.rm("old.txt")
    _write(repo, "new.txt", text)
    repo.add(["new.txt"])
    repo.config_set("status", "renames", "false")

    disabled = _run(repo, "status", "-s")
    enabled = _run(repo, "status", "-s", "--find-renames=50%")

    assert "A  new.txt\n" in disabled.stdout and "D  old.txt\n" in disabled.stdout
    assert enabled.stdout == "R  old.txt -> new.txt\n"


def test_explicit_no_renames_plus_find_renames_is_rename_only(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("status", "renames", "copies")

    result = _run(repo, "status", "-s", "--no-renames", "--find-renames=50%")

    assert "A  copy.txt\n" in result.stdout
    assert "M  old.txt\n" in result.stdout
    assert "C " not in result.stdout


def test_diff_renames_copies_is_used_when_status_policy_is_unset(tmp_path: Path) -> None:
    repo = _base_repo(tmp_path)
    _stage_modified_copy(repo)
    repo.config_set("diff", "renames", "copies")

    result = _run(repo, "status", "-s")

    assert "C  old.txt -> copy.txt\n" in result.stdout
    assert "M  old.txt\n" in result.stdout
