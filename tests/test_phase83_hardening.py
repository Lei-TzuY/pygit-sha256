"""Hardening regressions for Phase 83 fork-point integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository, fork_point, repack
from pygit.objects import BlobObject, CommitObject, TreeObject


def _repo(tmp_path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject())
    return repo, tree


def _commit(repo: Repository, tree: str, parents: list[str], label: str) -> str:
    return repo.store.write(CommitObject(tree=tree, parents=parents, message=label))


def _line(old: str, new: str, timestamp: int, message: str) -> str:
    return f"{old} {new} Tester <tester@example.com> {timestamp} +0000\t{message}\n"


def _rewritten(repo: Repository, tree: str) -> dict[str, str]:
    root = _commit(repo, tree, [], "root")
    old_tip = _commit(repo, tree, [root], "old-tip")
    topic = _commit(repo, tree, [old_tip], "topic")
    rewritten = _commit(repo, tree, [root], "rewritten")

    repo.refs.set_branch("upstream", old_tip, message="old upstream")
    repo.refs.set_branch("upstream", rewritten, message="rewrite upstream")
    repo.refs.set_branch("topic", topic, message="topic")
    repo.refs.set_head_symbolic("topic")
    return {"old_tip": old_tip, "topic": topic, "rewritten": rewritten}


def test_derived_commit_uses_shared_numeric_reflog_selector(tmp_path: Path) -> None:
    repo, tree = _repo(tmp_path)
    ids = _rewritten(repo, tree)
    assert fork_point(repo, "upstream", "topic@{0}") == ids["old_tip"]


def test_selector_and_historical_tip_work_after_repack(tmp_path: Path) -> None:
    repo, tree = _repo(tmp_path)
    ids = _rewritten(repo, tree)
    result = repack(repo, all_objects=True, delete_redundant=True)
    assert result.object_count > 0
    loose = repo.store.root / ids["old_tip"][:2] / ids["old_tip"][2:]
    assert not loose.exists()
    assert fork_point(repo, "upstream", "topic@{0}") == ids["old_tip"]


def test_missing_unrelated_reflog_tip_is_ignored(tmp_path: Path) -> None:
    repo, tree = _repo(tmp_path)
    ids = _rewritten(repo, tree)
    missing = "f" * 64
    log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    log.write_text(
        log.read_text(encoding="utf-8")
        + _line(ids["rewritten"], missing, 100, "missing")
        + _line(missing, ids["rewritten"], 101, "restore"),
        encoding="utf-8",
    )
    assert fork_point(repo, "upstream", "topic") == ids["old_tip"]


def test_existing_non_commit_reflog_tip_fails_loudly(tmp_path: Path) -> None:
    repo, tree = _repo(tmp_path)
    ids = _rewritten(repo, tree)
    blob = repo.store.write(BlobObject(b"not a commit"))
    log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    log.write_text(
        log.read_text(encoding="utf-8")
        + _line(ids["rewritten"], blob, 100, "invalid type"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="non-commit object"):
        fork_point(repo, "upstream", "topic")
