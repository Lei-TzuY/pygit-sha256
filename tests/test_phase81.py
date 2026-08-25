"""Phase 81 tests: reflog-aware ``merge-base --fork-point``."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Dict, Optional, Tuple

import pytest

from pygit import Repository, fork_point, merge_bases, repack
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


ZERO = "0" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(
    repo: Repository,
    label: str,
    parents: Tuple[str, ...] = (),
    timestamp: int = 1,
) -> str:
    blob = repo.store.write(BlobObject((label + "\n").encode()))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=list(parents),
            author=identity,
            committer=identity,
            message=label,
        )
    )


def _line(old: str, new: str, timestamp: int, message: str) -> str:
    return f"{old} {new} Tester <tester@example.com> {timestamp} +0000\t{message}\n"


def _write_log(repo: Repository, ref: str, text: str) -> Path:
    relative = "HEAD" if ref == "HEAD" else ref
    path = repo.pygit_dir / "logs" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _rewritten_history(repo: Repository) -> Dict[str, str]:
    root = _commit(repo, "root", timestamp=100)
    b0 = _commit(repo, "b0", (root,), 200)
    b1 = _commit(repo, "b1", (b0,), 300)
    b2 = _commit(repo, "b2", (b1,), 400)

    c1 = _commit(repo, "c1", (root,), 500)
    c2 = _commit(repo, "c2", (c1,), 600)
    topic = _commit(repo, "topic", (b0,), 700)

    repo.refs.set_branch("upstream", c2)
    repo.refs.set_branch("topic", topic)
    repo.refs.set_head_symbolic("topic")

    upstream_log = (
        _line(ZERO, root, 100, "create")
        + _line(root, b0, 200, "advance b0")
        + _line(b0, b1, 300, "advance b1")
        + _line(b1, b2, 400, "advance b2")
        + _line(b2, c1, 500, "rewrite")
        + _line(c1, c2, 600, "advance c2")
    )
    _write_log(repo, "refs/heads/upstream", upstream_log)
    _write_log(repo, "refs/heads/topic", _line(ZERO, topic, 700, "topic"))
    _write_log(repo, "HEAD", _line(ZERO, topic, 700, "checkout topic"))

    return {
        "root": root,
        "b0": b0,
        "b1": b1,
        "b2": b2,
        "c1": c1,
        "c2": c2,
        "topic": topic,
    }


def test_fork_point_recovers_pre_rewrite_branch_point(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _rewritten_history(repo)

    assert merge_bases(repo, "upstream", "topic") == [h["root"]]
    assert fork_point(repo, "upstream", "topic") == h["b0"]
    assert fork_point(repo, "refs/heads/upstream", "topic") == h["b0"]


def test_cli_defaults_derived_commit_to_head_and_accepts_shared_revisions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _rewritten_history(repo)

    defaulted = _run(repo, "merge-base", "--fork-point", "upstream")
    explicit = _run(repo, "merge-base", "--fork-point", "upstream", "HEAD@{0}")

    assert defaulted.returncode == 0, defaulted.stderr.decode()
    assert explicit.returncode == 0, explicit.stderr.decode()
    assert defaulted.stdout == f"{h['b0']}\n".encode()
    assert explicit.stdout == f"{h['b0']}\n".encode()


def test_no_reflog_or_no_eligible_history_returns_no_fork_point(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = _commit(repo, "root")
    left = _commit(repo, "left", (root,), 2)
    right_root = _commit(repo, "other-root", timestamp=3)
    right = _commit(repo, "right", (right_root,), 4)

    repo.refs.set_branch("nolog", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_head_symbolic("right")

    assert fork_point(repo, "nolog", "right") is None
    missing = _run(repo, "merge-base", "--fork-point", "nolog", "right")
    assert missing.returncode == 1
    assert missing.stdout == b""

    _write_log(repo, "refs/heads/nolog", _line(ZERO, left, 2, "create"))
    assert fork_point(repo, "nolog", "right") is None


def test_multiple_incomparable_historical_candidates_are_ambiguous(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = _commit(repo, "root")
    left = _commit(repo, "left", (root,), 2)
    right = _commit(repo, "right", (root,), 3)
    rewritten = _commit(repo, "rewritten", (root,), 4)
    derived = _commit(repo, "merge", (left, right), 5)

    repo.refs.set_branch("upstream", rewritten)
    repo.refs.set_branch("derived", derived)
    repo.refs.set_head_symbolic("derived")
    _write_log(
        repo,
        "refs/heads/upstream",
        _line(ZERO, left, 2, "left")
        + _line(left, right, 3, "jump right")
        + _line(right, rewritten, 4, "rewrite"),
    )

    assert fork_point(repo, "upstream", "derived") is None
    result = _run(repo, "merge-base", "--fork-point", "upstream", "derived")
    assert result.returncode == 1
    assert result.stdout == b""


def test_pruned_unrelated_reflog_entries_are_ignored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _rewritten_history(repo)
    missing_oid = "f" * 64
    path = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    path.write_text(
        path.read_text(encoding="utf-8")
        + _line(h["c2"], missing_oid, 800, "missing historical state")
        + _line(missing_oid, h["c2"], 900, "restore"),
        encoding="utf-8",
    )

    assert fork_point(repo, "upstream", "topic") == h["b0"]


def test_fork_point_works_when_history_is_packed_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _rewritten_history(repo)

    result = repack(repo, all_objects=True, delete_redundant=True)
    assert result.object_count > 0
    loose = repo.store.root / h["b0"][:2] / h["b0"][2:]
    assert not loose.exists()

    assert fork_point(repo, "upstream", "topic") == h["b0"]


def test_malformed_reflog_fails_closed_before_graph_guessing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _rewritten_history(repo)
    path = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    path.write_text("not a reflog record\n", encoding="utf-8")

    with pytest.raises((ValueError, RuntimeError)):
        fork_point(repo, "upstream", "topic")

    cli = _run(repo, "merge-base", "--fork-point", "upstream", "topic")
    assert cli.returncode == 1
    assert b"error:" in cli.stderr


def test_fork_point_is_read_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _rewritten_history(repo)
    upstream_log = repo.pygit_dir / "logs" / "refs" / "heads" / "upstream"
    before_log = upstream_log.read_bytes()
    before_head = repo.refs.get_head()
    before_refs = (repo.refs.get_branch("upstream"), repo.refs.get_branch("topic"))
    before_objects = set(repo.store.all_shas())

    assert fork_point(repo, "upstream", "topic") == h["b0"]

    assert upstream_log.read_bytes() == before_log
    assert repo.refs.get_head() == before_head
    assert (repo.refs.get_branch("upstream"), repo.refs.get_branch("topic")) == before_refs
    assert set(repo.store.all_shas()) == before_objects


def test_fork_point_requires_a_current_ref_even_if_a_log_file_exists(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit = _commit(repo, "orphan")
    _write_log(repo, "refs/heads/deleted", _line(ZERO, commit, 1, "old branch"))

    with pytest.raises(KeyError, match="existing ref"):
        fork_point(repo, "deleted", commit)
