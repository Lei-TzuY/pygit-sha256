"""Phase 51 tests: transactional ref updates and symbolic refs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pygit import Repository
from pygit.command import dispatch
from pygit.objects import BlobObject
from pygit.ref_transaction import (
    RefUpdate,
    delete_symbolic_ref,
    set_symbolic_ref,
    symbolic_target,
    update_ref,
    update_refs,
)
from pygit.refs import ZERO_SHA


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, text: str, message: str) -> str:
    path = repo.worktree / "f.txt"
    path.write_text(text, encoding="utf-8")
    repo.add(["f.txt"])
    return repo.commit(
        message,
        author_name="Tester",
        author_email="tester@example.com",
    )


def _commits(repo: Repository) -> tuple[str, str]:
    first = _commit(repo, "one\n", "first")
    second = _commit(repo, "two\n", "second")
    return first, second


class TestUpdateRef:
    def test_create_compare_and_swap_and_reflog(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        update_ref(repo, "refs/heads/topic", one, old_oid=ZERO_SHA, message="create topic")
        assert repo.refs.get_branch("topic") == one
        update_ref(repo, "refs/heads/topic", two, old_oid=one, message="advance topic")
        assert repo.refs.get_branch("topic") == two
        entries = repo.refs.read_reflog("refs/heads/topic")
        assert entries[0].old_sha == one
        assert entries[0].new_sha == two
        assert entries[0].message == "advance topic"
        with pytest.raises(RuntimeError, match="expected"):
            update_ref(repo, "refs/heads/topic", one, old_oid=one)
        assert repo.refs.get_branch("topic") == two

    def test_batch_is_prevalidated_before_any_mutation(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        repo.refs.set_branch("a", one)
        repo.refs.set_branch("b", two)
        with pytest.raises(RuntimeError, match="expected"):
            update_refs(repo, [
                RefUpdate("update", "refs/heads/a", two, one),
                RefUpdate("update", "refs/heads/b", one, one),
            ])
        assert repo.refs.get_branch("a") == one
        assert repo.refs.get_branch("b") == two

    def test_create_delete_verify_and_duplicate_transaction_rejection(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, _ = _commits(repo)
        update_refs(repo, [RefUpdate("create", "refs/tags/v1", one, ZERO_SHA)])
        assert repo.refs.get_tag("v1") == one
        update_refs(repo, [RefUpdate("verify", "refs/tags/v1", None, one)])
        update_refs(repo, [RefUpdate("delete", "refs/tags/v1", None, one)])
        assert repo.refs.get_tag("v1") is None
        with pytest.raises(ValueError, match="multiple updates"):
            update_refs(repo, [
                RefUpdate("create", "refs/heads/x", one, ZERO_SHA),
                RefUpdate("update", "refs/heads/x", one),
            ])

    def test_head_dereferences_by_default_and_no_deref_detaches(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        assert repo.refs.get_branch("main") == two
        assert repo.refs.get_head() == "ref: refs/heads/main"
        update_ref(repo, "HEAD", one, old_oid=two)
        assert repo.refs.get_branch("main") == one
        assert repo.refs.get_head() == "ref: refs/heads/main"
        update_ref(repo, "HEAD", two, old_oid=None, deref=False)
        assert repo.refs.get_head() == two
        assert repo.refs.get_branch("main") == one
        assert repo.refs.is_detached()

    def test_branch_rejects_blob_but_tag_accepts_blob(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        blob = repo.store.write(BlobObject(b"payload"))
        with pytest.raises(ValueError, match="non-commit"):
            update_ref(repo, "refs/heads/not-a-commit", blob)
        update_ref(repo, "refs/tags/blob-tag", blob)
        assert repo.refs.get_tag("blob-tag") == blob

    def test_rejects_missing_object_and_invalid_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(KeyError, match="Object not found"):
            update_ref(repo, "refs/heads/missing", "a" * 64)
        with pytest.raises(ValueError):
            update_ref(repo, "../escape", repo.store.write(BlobObject(b"x")))


class TestSymbolicRef:
    def test_read_set_and_retarget_head(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        repo.refs.set_branch("next", one)
        assert symbolic_target(repo, "HEAD") == "refs/heads/main"
        set_symbolic_ref(repo, "HEAD", "refs/heads/next", message="switch symbolic head")
        assert symbolic_target(repo, "HEAD") == "refs/heads/next"
        assert repo.refs.resolve_head() == one
        assert repo.refs.read_reflog("HEAD")[0].message == "switch symbolic head"
        assert two != one

    def test_symbolic_alias_is_dereferenced_by_update_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        repo.refs.set_branch("alias-target", one)
        set_symbolic_ref(repo, "refs/aliases/current", "refs/heads/alias-target")
        update_ref(repo, "refs/aliases/current", two, old_oid=one)
        assert repo.refs.get_branch("alias-target") == two
        assert symbolic_target(repo, "refs/aliases/current") == "refs/heads/alias-target"

    def test_cycle_is_rejected_and_head_cannot_be_deleted(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        set_symbolic_ref(repo, "refs/aliases/a", "refs/aliases/b")
        with pytest.raises(RuntimeError, match="cycle"):
            set_symbolic_ref(repo, "refs/aliases/b", "refs/aliases/a")
        with pytest.raises(ValueError, match="HEAD"):
            delete_symbolic_ref(repo, "HEAD")

    def test_delete_non_head_symbolic_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        set_symbolic_ref(repo, "refs/aliases/current", "refs/heads/main")
        delete_symbolic_ref(repo, "refs/aliases/current")
        assert symbolic_target(repo, "refs/aliases/current") is None


class TestPhase51CLI:
    def test_update_ref_stdin_transaction(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        monkeypatch.setattr("sys.stdin", io.StringIO(
            f"create refs/heads/a {one}\n"
            f"create refs/heads/b {two}\n"
            f"verify refs/heads/main {two}\n"
        ))
        assert dispatch(["update-ref", "--stdin", "-m", "batch"]) == 0
        assert repo.refs.get_branch("a") == one
        assert repo.refs.get_branch("b") == two

    def test_symbolic_ref_cli_and_cas_failure(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        one, two = _commits(repo)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        assert dispatch(["symbolic-ref", "HEAD"]) == 0
        assert capsys.readouterr().out.strip() == "refs/heads/main"
        assert dispatch(["update-ref", "refs/heads/main", one, ZERO_SHA]) == 1
        assert "error:" in capsys.readouterr().err
        assert repo.refs.get_branch("main") == two
        repo.refs.set_branch("topic", one)
        assert dispatch(["symbolic-ref", "HEAD", "refs/heads/topic", "-m", "retarget"]) == 0
        assert symbolic_target(repo, "HEAD") == "refs/heads/topic"
