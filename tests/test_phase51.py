"""Phase 51 tests: transactional ref updates and symbolic refs."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pygit import Repository
from pygit.command import dispatch
from pygit.objects import BlobObject
from pygit.ref_transaction import RefUpdate, set_symbolic_ref, symbolic_target, update_ref, update_refs
from pygit.refs import ZERO_SHA


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _oids(repo: Repository) -> tuple[str, str, str]:
    return tuple(repo.store.write(BlobObject(value)) for value in (b"one", b"two", b"three"))


class TestUpdateRef:
    def test_create_compare_and_swap_and_reflog(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two, _ = _oids(repo)
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
        one, two, three = _oids(repo)
        update_ref(repo, "refs/heads/a", one)
        update_ref(repo, "refs/heads/b", two)
        with pytest.raises(RuntimeError, match="expected"):
            update_refs(repo, [
                RefUpdate("update", "refs/heads/a", three, one),
                RefUpdate("update", "refs/heads/b", three, one),
            ])
        assert repo.refs.get_branch("a") == one
        assert repo.refs.get_branch("b") == two

    def test_create_delete_verify_and_duplicate_transaction_rejection(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, _, _ = _oids(repo)
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
        one, two, _ = _oids(repo)
        update_ref(repo, "refs/heads/main", one)
        assert repo.refs.get_head() == "ref: refs/heads/main"
        update_ref(repo, "HEAD", two, old_oid=one)
        assert repo.refs.get_branch("main") == two
        assert repo.refs.get_head() == "ref: refs/heads/main"
        update_ref(repo, "HEAD", one, old_oid=None, deref=False)
        assert repo.refs.get_head() == one
        assert repo.refs.get_branch("main") == two

    def test_rejects_missing_object_and_invalid_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        with pytest.raises(KeyError, match="Object not found"):
            update_ref(repo, "refs/heads/missing", "a" * 64)
        with pytest.raises(ValueError):
            update_ref(repo, "../escape", repo.store.write(BlobObject(b"x")))


class TestSymbolicRef:
    def test_read_set_and_retarget_head(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two, _ = _oids(repo)
        update_ref(repo, "refs/heads/main", one)
        update_ref(repo, "refs/heads/next", two)
        assert symbolic_target(repo, "HEAD") == "refs/heads/main"
        set_symbolic_ref(repo, "HEAD", "refs/heads/next", message="switch symbolic head")
        assert symbolic_target(repo, "HEAD") == "refs/heads/next"
        assert repo.refs.resolve_head() == two
        assert repo.refs.read_reflog("HEAD")[0].message == "switch symbolic head"

    def test_symbolic_alias_is_dereferenced_by_update_ref(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        one, two, _ = _oids(repo)
        update_ref(repo, "refs/heads/main", one)
        set_symbolic_ref(repo, "refs/aliases/current", "refs/heads/main")
        update_ref(repo, "refs/aliases/current", two, old_oid=one)
        assert repo.refs.get_branch("main") == two
        assert symbolic_target(repo, "refs/aliases/current") == "refs/heads/main"


class TestPhase51CLI:
    def test_update_ref_stdin_transaction(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        one, two, _ = _oids(repo)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        monkeypatch.setattr("sys.stdin", io.StringIO(
            f"create refs/heads/a {one}\n"
            f"create refs/heads/b {two}\n"
        ))
        assert dispatch(["update-ref", "--stdin", "-m", "batch"]) == 0
        assert repo.refs.get_branch("a") == one
        assert repo.refs.get_branch("b") == two

    def test_symbolic_ref_cli_and_cas_failure(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        one, two, _ = _oids(repo)
        monkeypatch.chdir(repo.worktree)
        update_ref(repo, "refs/heads/main", one)
        capsys.readouterr()
        assert dispatch(["symbolic-ref", "HEAD"]) == 0
        assert capsys.readouterr().out.strip() == "refs/heads/main"
        assert dispatch(["update-ref", "refs/heads/main", two, "f" * 64]) == 1
        assert "error:" in capsys.readouterr().err
        assert repo.refs.get_branch("main") == one
        assert dispatch(["symbolic-ref", "HEAD", "refs/heads/topic", "-m", "retarget"]) == 0
        assert symbolic_target(repo, "HEAD") == "refs/heads/topic"
