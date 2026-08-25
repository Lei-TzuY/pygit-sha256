"""Phase 71 tests: safe pruning of expired unreachable loose objects."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pygit import Repository, prune
from pygit.index import IndexEntry
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.prune_cli import parse_expire


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _age(repo: Repository, oid: str, seconds: int = 30 * 24 * 60 * 60) -> None:
    stamp = time.time() - seconds
    os.utime(_path(repo, oid), (stamp, stamp))


def _commit(repo: Repository, payload: bytes, message: str) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    ident = Identity("Tester", "tester@example.com", 1, "+0000")
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=ident,
            committer=ident,
            message=message,
        )
    )
    return blob, tree, commit


def test_expire_now_prunes_old_unreachable_loose_object(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"dangling\n"))
    _age(repo, oid)

    result = prune(repo, expire_before=time.time())

    assert result.oids == (oid,)
    assert result.pruned == 1
    assert not _path(repo, oid).exists()


def test_default_two_week_grace_keeps_recent_unreachable_object(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"recent\n"))

    result = prune(repo)

    assert result.pruned == 0
    assert result.kept_recent == (oid,)
    assert _path(repo, oid).exists()


def test_current_ref_graph_is_never_pruned(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, tree, commit = _commit(repo, b"reachable\n", "current")
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    for oid in (blob, tree, commit):
        _age(repo, oid)

    result = prune(repo, expire_before=time.time())

    assert not ({blob, tree, commit} & set(result.oids))
    assert all(_path(repo, oid).exists() for oid in (blob, tree, commit))


def test_reflog_history_retains_dropped_commit_closure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old_blob, old_tree, old_commit = _commit(repo, b"old\n", "old")
    new_blob, new_tree, new_commit = _commit(repo, b"new\n", "new")
    repo.refs.set_branch("main", old_commit)
    repo.refs.set_branch("main", new_commit)
    repo.refs.set_head_symbolic("main")
    for oid in (old_blob, old_tree, old_commit, new_blob, new_tree, new_commit):
        _age(repo, oid)

    result = prune(repo, expire_before=time.time())

    assert result.reflog_roots >= 2
    assert all(_path(repo, oid).exists() for oid in (old_blob, old_tree, old_commit))
    assert all(_path(repo, oid).exists() for oid in (new_blob, new_tree, new_commit))


def test_index_entry_is_a_retention_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"staged\n"))
    repo.index.entries["staged.txt"] = IndexEntry("staged.txt", oid, size=7)
    repo.index.save()
    _age(repo, oid)

    result = prune(repo, expire_before=time.time())

    assert oid not in result.oids
    assert _path(repo, oid).exists()


def test_extra_head_retains_unreferenced_commit_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, tree, commit = _commit(repo, b"keep me\n", "extra")
    for oid in (blob, tree, commit):
        _age(repo, oid)

    result = prune(repo, expire_before=time.time(), extra_heads=[commit])

    assert not ({blob, tree, commit} & set(result.oids))
    assert all(_path(repo, oid).exists() for oid in (blob, tree, commit))


def test_dry_run_reports_but_does_not_delete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"dry\n"))
    _age(repo, oid)

    result = prune(repo, expire_before=time.time(), dry_run=True)

    assert result.oids == (oid,)
    assert result.pruned == 0
    assert _path(repo, oid).exists()


def test_malformed_reflog_aborts_before_any_unlink(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"would-prune\n"))
    _age(repo, oid)
    logs = repo.pygit_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "HEAD").write_text("not a valid reflog\n", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed reflog"):
        prune(repo, expire_before=time.time())

    assert _path(repo, oid).exists()


def test_malformed_loose_object_is_preserved(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"corrupt-me\n"))
    _age(repo, oid)
    _path(repo, oid).write_bytes(b"not-zlib")

    result = prune(repo, expire_before=time.time())

    assert result.pruned == 0
    assert result.skipped_loose == (oid,)
    assert _path(repo, oid).exists()


def test_expire_parser_and_installed_cli(tmp_path: Path) -> None:
    assert parse_expire("never", now=1000) == float("-inf")
    assert parse_expire("2.days.ago", now=200000) == 27200
    with pytest.raises(ValueError, match="--expire expects"):
        parse_expire("yesterday", now=1000)

    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"cli\n"))
    _age(repo, oid)
    result = subprocess.run(
        [sys.executable, "-m", "pygit", "prune", "--expire=now", "--dry-run", "--verbose"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert oid in result.stdout.splitlines()
    assert _path(repo, oid).exists()
