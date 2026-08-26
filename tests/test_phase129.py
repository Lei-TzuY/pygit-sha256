"""Phase 129 tests: replay conflicts populate persistent index stages 1-3."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository, checkout_index
from pygit.index_plumbing import ls_files
from pygit.revision import resolve_revision


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _cherry_conflicted_repo(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "conflict.txt", "base\n")
    repo.add(["conflict.txt"])
    base_commit = repo.commit("base")
    base_blob = repo._commit_tree_entries(base_commit)["conflict.txt"][0]

    repo.branch("source")
    _write(repo, "conflict.txt", "theirs\n")
    repo.add(["conflict.txt"])
    source_commit = repo.commit("source change")
    source_blob = repo._commit_tree_entries(source_commit)["conflict.txt"][0]

    repo.checkout("main")
    _write(repo, "conflict.txt", "ours\n")
    repo.add(["conflict.txt"])
    ours_commit = repo.commit("ours change")
    ours_blob = repo._commit_tree_entries(ours_commit)["conflict.txt"][0]

    result = repo.cherry_pick(source_commit)
    assert result["status"] == "conflicts"
    assert result["conflicts"] == ["conflict.txt"]
    return repo, base_blob, ours_blob, source_blob, ours_commit, source_commit


def _rebase_conflicted_repo(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "conflict.txt", "base\n")
    repo.add(["conflict.txt"])
    base_commit = repo.commit("base")
    base_blob = repo._commit_tree_entries(base_commit)["conflict.txt"][0]

    repo.branch("topic")
    _write(repo, "conflict.txt", "topic\n")
    repo.add(["conflict.txt"])
    topic_commit = repo.commit("topic change")
    topic_blob = repo._commit_tree_entries(topic_commit)["conflict.txt"][0]

    repo.checkout("main")
    _write(repo, "conflict.txt", "upstream\n")
    repo.add(["conflict.txt"])
    upstream_commit = repo.commit("upstream change")
    upstream_blob = repo._commit_tree_entries(upstream_commit)["conflict.txt"][0]

    repo.checkout("topic")
    result = repo.rebase("main")
    assert result["status"] == "conflicts"
    assert result["conflicts"] == ["conflict.txt"]
    return repo, base_blob, upstream_blob, topic_blob, topic_commit, upstream_commit


def test_cherry_pick_conflict_populates_exact_stages_and_plumbing(tmp_path: Path) -> None:
    repo, base_blob, ours_blob, theirs_blob, _ours_commit, _source_commit = _cherry_conflicted_repo(tmp_path)

    assert repo.index.get("conflict.txt") is None
    assert repo.index.get("conflict.txt", 1).sha == base_blob
    assert repo.index.get("conflict.txt", 2).sha == ours_blob
    assert repo.index.get("conflict.txt", 3).sha == theirs_blob
    assert resolve_revision(repo, ":1:conflict.txt") == base_blob
    assert resolve_revision(repo, ":2:conflict.txt") == ours_blob
    assert resolve_revision(repo, ":3:conflict.txt") == theirs_blob
    assert ls_files(repo, stage=True) == [
        f"100644 {base_blob} 1\tconflict.txt",
        f"100644 {ours_blob} 2\tconflict.txt",
        f"100644 {theirs_blob} 3\tconflict.txt",
    ]

    checkout_index(repo, ["conflict.txt"], stage=2, prefix="ours-side")
    checkout_index(repo, ["conflict.txt"], stage=3, prefix="theirs-side")
    assert (repo.worktree / "ours-side" / "conflict.txt").read_text(encoding="utf-8") == "ours\n"
    assert (repo.worktree / "theirs-side" / "conflict.txt").read_text(encoding="utf-8") == "theirs\n"


def test_cherry_pick_continue_collapses_stages_and_commits_resolution(tmp_path: Path) -> None:
    repo, _base, _ours, _theirs, ours_commit, _source_commit = _cherry_conflicted_repo(tmp_path)

    _write(repo, "conflict.txt", "resolved cherry\n")
    repo.add(["conflict.txt"])
    assert repo.index.stage_entries("conflict.txt") == []
    assert repo.status()["conflicts"] == []

    result = repo.cherry_pick_continue()
    assert result["status"] == "picked"
    assert result["sha"] != ours_commit
    assert repo.index.stage_entries() == []
    assert not (repo.pygit_dir / "CHERRY_PICK_STATE").exists()
    assert repo._commit_tree_entries(result["sha"])["conflict.txt"][0] == repo.index.get("conflict.txt").sha


def test_cherry_pick_abort_restores_ours_and_clears_unmerged(tmp_path: Path) -> None:
    repo, _base, ours_blob, _theirs, ours_commit, _source_commit = _cherry_conflicted_repo(tmp_path)

    result = repo.cherry_pick_abort()
    assert result == {"status": "aborted", "sha": ours_commit, "conflicts": []}
    assert repo.refs.resolve_head() == ours_commit
    assert repo.index.get("conflict.txt").sha == ours_blob
    assert repo.index.stage_entries() == []
    assert repo.status()["conflicts"] == []
    assert (repo.worktree / "conflict.txt").read_text(encoding="utf-8") == "ours\n"


def test_rebase_conflict_uses_source_parent_upstream_and_source_stages(tmp_path: Path) -> None:
    repo, base_blob, upstream_blob, topic_blob, _topic_commit, upstream_commit = _rebase_conflicted_repo(tmp_path)

    assert repo.refs.resolve_head() == upstream_commit
    assert repo.index.get("conflict.txt") is None
    assert repo.index.get("conflict.txt", 1).sha == base_blob
    assert repo.index.get("conflict.txt", 2).sha == upstream_blob
    assert repo.index.get("conflict.txt", 3).sha == topic_blob
    assert repo.status()["operation"] == "rebase"


def test_rebase_skip_clears_skipped_stages_before_finishing(tmp_path: Path) -> None:
    repo, _base, upstream_blob, _topic_blob, _topic_commit, upstream_commit = _rebase_conflicted_repo(tmp_path)

    result = repo.rebase_skip()
    assert result == {"status": "rebased", "sha": upstream_commit, "conflicts": []}
    assert repo.index.stage_entries() == []
    assert repo.index.get("conflict.txt").sha == upstream_blob
    assert repo.status()["conflicts"] == []
    assert repo.status()["operation"] is None
    assert (repo.worktree / "conflict.txt").read_text(encoding="utf-8") == "upstream\n"


def test_rebase_abort_restores_original_topic_and_clears_stages(tmp_path: Path) -> None:
    repo, _base, _upstream_blob, topic_blob, topic_commit, _upstream_commit = _rebase_conflicted_repo(tmp_path)

    result = repo.rebase_abort()
    assert result == {"status": "aborted", "sha": topic_commit, "conflicts": []}
    assert repo.refs.resolve_head() == topic_commit
    assert repo.refs.current_branch() == "topic"
    assert repo.index.get("conflict.txt").sha == topic_blob
    assert repo.index.stage_entries() == []
    assert repo.status()["operation"] is None
    assert (repo.worktree / "conflict.txt").read_text(encoding="utf-8") == "topic\n"


def test_cherry_pick_modify_delete_omits_missing_stage_three(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "gone.txt", "base\n")
    repo.add(["gone.txt"])
    base_commit = repo.commit("base")
    base_blob = repo._commit_tree_entries(base_commit)["gone.txt"][0]

    repo.branch("delete-source")
    repo.rm("gone.txt")
    delete_commit = repo.commit("delete source")

    repo.checkout("main")
    _write(repo, "gone.txt", "ours changed\n")
    repo.add(["gone.txt"])
    ours_commit = repo.commit("modify ours")
    ours_blob = repo._commit_tree_entries(ours_commit)["gone.txt"][0]

    result = repo.cherry_pick(delete_commit)
    assert result["status"] == "conflicts"
    assert repo.index.get("gone.txt") is None
    assert repo.index.get("gone.txt", 1).sha == base_blob
    assert repo.index.get("gone.txt", 2).sha == ours_blob
    assert repo.index.get("gone.txt", 3) is None
    assert [entry.stage for entry in repo.index.stage_entries("gone.txt")] == [1, 2]
