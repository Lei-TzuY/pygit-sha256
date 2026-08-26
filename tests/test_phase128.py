"""Phase 128 tests: high-level merge conflicts populate index stages 1-3."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository, checkout_index
from pygit.index_plumbing import ls_files
from pygit.revision import resolve_revision


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _conflicted_repo(tmp_path: Path) -> tuple[Repository, str, str, str]:
    repo = Repository.init(str(tmp_path / "repo"))

    _write(repo, "conflict.txt", "base\n")
    repo.add(["conflict.txt"])
    base_commit = repo.commit("base")
    base_blob = repo._commit_tree_entries(base_commit)["conflict.txt"][0]

    repo.branch("feature")
    _write(repo, "conflict.txt", "theirs\n")
    repo.add(["conflict.txt"])
    theirs_commit = repo.commit("theirs")
    theirs_blob = repo._commit_tree_entries(theirs_commit)["conflict.txt"][0]

    repo.checkout("main")
    _write(repo, "conflict.txt", "ours\n")
    repo.add(["conflict.txt"])
    ours_commit = repo.commit("ours")
    ours_blob = repo._commit_tree_entries(ours_commit)["conflict.txt"][0]

    result = repo.merge("feature")
    assert result["status"] == "conflicts"
    assert result["conflicts"] == ["conflict.txt"]
    return repo, base_blob, ours_blob, theirs_blob


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_real_merge_conflict_populates_base_ours_theirs_stages(tmp_path: Path) -> None:
    repo, base_blob, ours_blob, theirs_blob = _conflicted_repo(tmp_path)

    assert repo.index.get("conflict.txt") is None
    assert repo.index.get("conflict.txt", 1).sha == base_blob
    assert repo.index.get("conflict.txt", 2).sha == ours_blob
    assert repo.index.get("conflict.txt", 3).sha == theirs_blob
    assert repo.index.has_unmerged("conflict.txt")

    assert resolve_revision(repo, ":1:conflict.txt") == base_blob
    assert resolve_revision(repo, ":2:conflict.txt") == ours_blob
    assert resolve_revision(repo, ":3:conflict.txt") == theirs_blob

    assert ls_files(repo, stage=True) == [
        f"100644 {base_blob} 1\tconflict.txt",
        f"100644 {ours_blob} 2\tconflict.txt",
        f"100644 {theirs_blob} 3\tconflict.txt",
    ]


def test_real_merge_conflict_feeds_checkout_index_stage_extraction(tmp_path: Path) -> None:
    repo, _base_blob, _ours_blob, _theirs_blob = _conflicted_repo(tmp_path)

    written = checkout_index(repo, ["conflict.txt"], stage=1, prefix="base")
    assert written == [repo.worktree / "base" / "conflict.txt"]
    assert (repo.worktree / "base" / "conflict.txt").read_text(encoding="utf-8") == "base\n"

    checkout_index(repo, ["conflict.txt"], stage=2, prefix="ours")
    checkout_index(repo, ["conflict.txt"], stage=3, prefix="theirs")
    assert (repo.worktree / "ours" / "conflict.txt").read_text(encoding="utf-8") == "ours\n"
    assert (repo.worktree / "theirs" / "conflict.txt").read_text(encoding="utf-8") == "theirs\n"


def test_installed_cli_sees_stages_created_by_porcelain_merge(tmp_path: Path) -> None:
    repo, base_blob, ours_blob, theirs_blob = _conflicted_repo(tmp_path)

    staged = _run(repo, "ls-files", "--stage")
    assert staged.returncode == 0, staged.stderr
    assert staged.stdout == (
        f"100644 {base_blob} 1\tconflict.txt\n"
        f"100644 {ours_blob} 2\tconflict.txt\n"
        f"100644 {theirs_blob} 3\tconflict.txt\n"
    )

    ours = _run(repo, "cat-file", "-p", ":2:conflict.txt")
    assert ours.returncode == 0, ours.stderr
    assert ours.stdout == "ours\n"

    checkout = _run(
        repo,
        "checkout-index",
        "--stage=3",
        "--prefix=cli-theirs/",
        "conflict.txt",
    )
    assert checkout.returncode == 0, checkout.stderr
    assert (repo.worktree / "cli-theirs" / "conflict.txt").read_text(encoding="utf-8") == "theirs\n"


def test_git_add_resolution_collapses_conflict_to_stage_zero_and_merge_commits(tmp_path: Path) -> None:
    repo, _base_blob, _ours_blob, _theirs_blob = _conflicted_repo(tmp_path)

    _write(repo, "conflict.txt", "resolved\n")
    repo.add(["conflict.txt"])

    resolved = repo.index.get("conflict.txt")
    assert resolved is not None
    assert repo.index.stage_entries("conflict.txt") == []
    assert repo.status()["conflicts"] == []

    merge_sha = repo.commit("resolve merge")
    merge_commit = repo._require_commit(merge_sha)
    assert len(merge_commit.parents) == 2
    assert repo.index.stage_entries() == []
    assert not (repo.pygit_dir / "MERGE_HEAD").exists()


def test_merge_abort_drops_unmerged_stages_and_restores_ours(tmp_path: Path) -> None:
    repo, _base_blob, ours_blob, _theirs_blob = _conflicted_repo(tmp_path)

    result = repo.merge_abort()

    assert result["status"] == "aborted"
    restored = repo.index.get("conflict.txt")
    assert restored is not None
    assert restored.sha == ours_blob
    assert repo.index.stage_entries() == []
    assert repo.status()["conflicts"] == []
    assert (repo.worktree / "conflict.txt").read_text(encoding="utf-8") == "ours\n"


def test_modify_delete_conflict_omits_missing_theirs_stage(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "gone.txt", "base\n")
    repo.add(["gone.txt"])
    base_commit = repo.commit("base")
    base_blob = repo._commit_tree_entries(base_commit)["gone.txt"][0]

    repo.branch("delete-side")
    repo.rm("gone.txt")
    repo.commit("delete theirs")

    repo.checkout("main")
    _write(repo, "gone.txt", "ours changed\n")
    repo.add(["gone.txt"])
    ours_commit = repo.commit("modify ours")
    ours_blob = repo._commit_tree_entries(ours_commit)["gone.txt"][0]

    result = repo.merge("delete-side")
    assert result["status"] == "conflicts"

    assert repo.index.get("gone.txt") is None
    assert repo.index.get("gone.txt", 1).sha == base_blob
    assert repo.index.get("gone.txt", 2).sha == ours_blob
    assert repo.index.get("gone.txt", 3) is None
    assert [entry.stage for entry in repo.index.stage_entries("gone.txt")] == [1, 2]
