"""Phase 149 tests: checkout-index creation, stat refresh, and quiet controls."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.index import IndexEntry
from pygit.objects import BlobObject


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _run(repo: Repository, *args: str):
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _tracked_repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "a.txt", "alpha\n")
    repo.add(["a.txt"])
    repo.commit("base")
    return repo


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


def test_no_create_skips_absent_target_without_reading_object(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.index.entries["missing-object.txt"] = IndexEntry(
        "missing-object.txt", "f" * 64, "100644", 0, 0.0
    )
    repo.index.save()

    result = _run(repo, "checkout-index", "--no-create", "missing-object.txt")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert not (repo.worktree / "missing-object.txt").exists()


def test_no_create_existing_target_still_obeys_force(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)
    _write(repo, "a.txt", "local\n")

    refused = _run(repo, "checkout-index", "--no-create", "a.txt")
    assert refused.returncode == 1
    assert "already exists" in refused.stderr
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "local\n"

    forced = _run(repo, "checkout-index", "--no-create", "--force", "a.txt")
    assert forced.returncode == 0, forced.stderr
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "alpha\n"


def test_create_can_override_prior_no_create_flag(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)
    (repo.worktree / "a.txt").unlink()

    result = _run(repo, "checkout-index", "--no-create", "--create", "a.txt")

    assert result.returncode == 0, result.stderr
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "alpha\n"


def test_index_refreshes_stage_zero_stat_after_normal_checkout(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)
    entry = repo.index.get("a.txt", 0)
    assert entry is not None
    entry.size = 0
    entry.mtime = 0.0
    repo.index.save()
    (repo.worktree / "a.txt").unlink()

    result = _run(repo, "checkout-index", "--index", "a.txt")

    assert result.returncode == 0, result.stderr
    reloaded = Repository(str(repo.worktree))
    refreshed = reloaded.index.get("a.txt", 0)
    assert refreshed is not None
    assert refreshed.size == len(b"alpha\n")
    assert refreshed.mtime > 0
    assert refreshed.mtime == (repo.worktree / "a.txt").lstat().st_mtime


def test_no_index_can_override_prior_index_flag(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)
    entry = repo.index.get("a.txt", 0)
    assert entry is not None
    entry.size = 0
    entry.mtime = 0.0
    repo.index.save()
    (repo.worktree / "a.txt").unlink()

    result = _run(repo, "checkout-index", "--index", "--no-index", "a.txt")

    assert result.returncode == 0, result.stderr
    unchanged = Repository(str(repo.worktree)).index.get("a.txt", 0)
    assert unchanged is not None
    assert unchanged.size == 0
    assert unchanged.mtime == 0.0


def test_index_updates_selected_conflict_stage_stat(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)
    entry = repo.index.get("conflict.txt", 2)
    assert entry is not None
    entry.size = 0
    entry.mtime = 0.0
    repo.index.save()
    (repo.worktree / "conflict.txt").unlink()

    result = _run(repo, "checkout-index", "--stage=2", "--index", "conflict.txt")

    assert result.returncode == 0, result.stderr
    reloaded = Repository(str(repo.worktree))
    refreshed = reloaded.index.get("conflict.txt", 2)
    assert refreshed is not None
    assert refreshed.size == len(b"ours\n")
    assert refreshed.mtime == (repo.worktree / "conflict.txt").lstat().st_mtime
    assert reloaded.index.get("conflict.txt", 1) is not None
    assert reloaded.index.get("conflict.txt", 3) is not None


def test_index_with_prefix_does_not_refresh_index_stat(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)
    entry = repo.index.get("a.txt", 0)
    assert entry is not None
    entry.size = 0
    entry.mtime = 0.0
    repo.index.save()
    (repo.worktree / "a.txt").unlink()

    result = _run(repo, "checkout-index", "--index", "--prefix=export/", "a.txt")

    assert result.returncode == 0, result.stderr
    assert (repo.worktree / "export" / "a.txt").read_text(encoding="utf-8") == "alpha\n"
    unchanged = Repository(str(repo.worktree)).index.get("a.txt", 0)
    assert unchanged is not None
    assert unchanged.size == 0
    assert unchanged.mtime == 0.0


def test_temp_ignores_no_create_and_index_stat_refresh(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)
    entry = repo.index.get("a.txt", 0)
    assert entry is not None
    entry.size = 0
    entry.mtime = 0.0
    repo.index.save()
    (repo.worktree / "a.txt").unlink()

    result = _run(repo, "checkout-index", "--temp", "--no-create", "--index", "a.txt")

    assert result.returncode == 0, result.stderr
    temp_name, tracked = result.stdout.rstrip("\n").split("\t", 1)
    assert tracked == "a.txt"
    assert (repo.worktree / temp_name).read_bytes() == b"alpha\n"
    assert not (repo.worktree / "a.txt").exists()
    unchanged = Repository(str(repo.worktree)).index.get("a.txt", 0)
    assert unchanged is not None
    assert unchanged.size == 0
    assert unchanged.mtime == 0.0


def test_quiet_suppresses_missing_path_and_existing_target_warnings(tmp_path: Path) -> None:
    repo = _tracked_repo(tmp_path)

    missing = _run(repo, "checkout-index", "--quiet", "not-there.txt")
    assert missing.returncode == 1
    assert missing.stderr == ""

    _write(repo, "a.txt", "local\n")
    existing = _run(repo, "checkout-index", "-q", "a.txt")
    assert existing.returncode == 1
    assert existing.stderr == ""
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "local\n"


def test_quiet_does_not_hide_object_store_failure(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.index.entries["broken.txt"] = IndexEntry(
        "broken.txt", "e" * 64, "100644", 0, 0.0
    )
    repo.index.save()

    result = _run(repo, "checkout-index", "--quiet", "broken.txt")

    assert result.returncode == 1
    assert result.stderr != ""
    assert "error:" in result.stderr


def test_help_advertises_phase149_controls(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    result = _run(repo, "checkout-index", "--help")

    assert result.returncode == 0
    for option in ("--no-create", "--create", "--index", "--no-index", "--quiet", "--no-quiet"):
        assert option in result.stdout
