from __future__ import annotations

import pytest

from pygit.fetch_head import read_fetch_head_oid, write_fetch_head
from pygit.repo import Repository
from pygit.revision import resolve_revision


def _commit(repo: Repository, content: str, message: str) -> str:
    path = repo.worktree / "a.txt"
    path.write_text(content, encoding="utf-8")
    repo.add(["a.txt"])
    return repo.commit(message, author_name="Test", author_email="test@example.com")


def test_read_fetch_head_returns_first_recorded_oid(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    first = "a" * 64
    second = "b" * 64
    (pygit_dir / "FETCH_HEAD").write_text(
        f"{first}\tnot-for-merge\tbranch 'dev' of x\n"
        f"{second}\t\tbranch 'main' of x\n",
        encoding="utf-8",
    )

    assert read_fetch_head_oid(pygit_dir) == first


def test_read_fetch_head_missing_and_empty_are_unresolved(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    assert read_fetch_head_oid(pygit_dir) is None
    (pygit_dir / "FETCH_HEAD").write_text("\n\n", encoding="utf-8")
    assert read_fetch_head_oid(pygit_dir) is None


def test_read_fetch_head_rejects_malformed_oid(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    (pygit_dir / "FETCH_HEAD").write_text(
        "not-an-object\t\tbranch 'main' of x\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Malformed FETCH_HEAD"):
        read_fetch_head_oid(pygit_dir)


def test_refstore_and_unified_revision_resolve_fetch_head(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    first = _commit(repo, "one\n", "one")
    second = _commit(repo, "two\n", "two")
    write_fetch_head(
        repo.pygit_dir,
        {"refs/heads/main": second},
        source="https://example.test/repo.git",
        mergeable=["refs/heads/main"],
    )

    assert repo.refs.resolve("FETCH_HEAD") == second
    assert resolve_revision(repo, "FETCH_HEAD") == second
    assert resolve_revision(repo, "FETCH_HEAD^") == first


def test_fetch_head_composes_with_tree_path_revision(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "one\n", "one")
    tip = _commit(repo, "two\n", "two")
    write_fetch_head(
        repo.pygit_dir,
        {"refs/heads/main": tip},
        source="origin",
        mergeable=["refs/heads/main"],
    )

    blob_oid = resolve_revision(repo, "FETCH_HEAD:a.txt")
    assert repo.store.read(blob_oid).serialize() == b"two\n"


def test_fetch_head_unknown_object_is_rejected_by_revision_layer(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    unknown = "f" * 64
    (repo.pygit_dir / "FETCH_HEAD").write_text(
        f"{unknown}\t\tbranch 'main' of origin\n",
        encoding="utf-8",
    )

    assert repo.refs.resolve("FETCH_HEAD") == unknown
    with pytest.raises(KeyError, match="Unknown object"):
        resolve_revision(repo, "FETCH_HEAD")
