"""
tests/test_phase46.py
=====================
Phase 46 tests: graph plumbing, merge-base, and show-ref.
"""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.objects import TagObject
from pygit.plumbing import (
    is_ancestor,
    list_refs,
    merge_bases,
    peel_oid,
    resolve_commit,
    verify_ref,
)


def _commit_file(repo: Repository, name: str, content: str, msg: str) -> str:
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    repo.add([name])
    return repo.commit(msg, author_name="Tester", author_email="t@e.com")


def _diverged_repo(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit_file(repo, "f.txt", "base\n", "base")
    repo.refs.set_branch("side", base)
    main_tip = _commit_file(repo, "f.txt", "main\n", "main")
    repo.checkout("side")
    side_tip = _commit_file(repo, "side.txt", "side\n", "side")
    return repo, base, main_tip, side_tip


class TestGraphPlumbing:
    def test_merge_base_on_diverged_history(self, tmp_path: Path) -> None:
        repo, base, main_tip, side_tip = _diverged_repo(tmp_path)

        assert merge_bases(repo, "main", "side") == [base]
        assert is_ancestor(repo, base, main_tip)
        assert is_ancestor(repo, base, side_tip)
        assert not is_ancestor(repo, main_tip, side_tip)

    def test_revision_modifiers_and_annotated_tags_are_peeled(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        first = _commit_file(repo, "f.txt", "one\n", "one")
        second = _commit_file(repo, "f.txt", "two\n", "two")

        tag = TagObject(
            target_sha=second,
            target_type=b"commit",
            tag_name="v2",
            message="annotated",
        )
        tag_sha = repo.store.write(tag)
        repo.refs.set_tag("v2", tag_sha)

        assert resolve_commit(repo, "v2") == second
        assert resolve_commit(repo, "HEAD~1") == first
        assert peel_oid(repo, tag_sha) == second
        assert merge_bases(repo, "v2", "HEAD~1") == [first]

    def test_shallow_boundary_stops_parent_walk(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        first = _commit_file(repo, "f.txt", "one\n", "one")
        second = _commit_file(repo, "f.txt", "two\n", "two")
        (repo.pygit_dir / "shallow").write_text(f"{second}\n", encoding="utf-8")

        assert merge_bases(repo, second, first) == []
        assert not is_ancestor(repo, first, second)


class TestReferencePlumbing:
    def test_list_refs_filters_and_suffix_patterns(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        sha = _commit_file(repo, "f.txt", "one\n", "one")
        repo.refs.set_branch("feature/topic", sha)
        repo.refs.set_tag("v1", sha)
        repo.refs.set_remote("origin", "main", sha)
        repo.refs.set_stash(sha)

        all_refs = {name: oid for oid, name in list_refs(repo)}
        assert all_refs["refs/heads/main"] == sha
        assert all_refs["refs/heads/feature/topic"] == sha
        assert all_refs["refs/tags/v1"] == sha
        assert all_refs["refs/remotes/origin/main"] == sha
        assert all_refs["refs/stash"] == sha

        heads = {name for _, name in list_refs(repo, heads=True)}
        assert heads == {"refs/heads/main", "refs/heads/feature/topic"}

        main_matches = {name for _, name in list_refs(repo, patterns=["main"])}
        assert main_matches == {"refs/heads/main", "refs/remotes/origin/main"}
        assert verify_ref(repo, "refs/tags/v1") == (sha, "refs/tags/v1")

    def test_list_refs_rejects_malformed_ref(self, tmp_path: Path) -> None:
        repo = Repository.init(str(tmp_path / "r"))
        bad = repo.pygit_dir / "refs" / "heads" / "broken"
        bad.write_text("not-an-object-id\n", encoding="utf-8")

        try:
            list_refs(repo)
        except RuntimeError as exc:
            assert "Malformed ref refs/heads/broken" in str(exc)
        else:
            raise AssertionError("malformed ref should fail loudly")


class TestExtendedEntrypoint:
    def test_merge_base_and_show_ref_commands(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        repo, base, _, _ = _diverged_repo(tmp_path)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        assert dispatch(["merge-base", "main", "side"]) == 0
        assert capsys.readouterr().out.strip() == base

        assert dispatch(["merge-base", "--is-ancestor", base, "main"]) == 0
        assert dispatch(["merge-base", "--is-ancestor", "main", "side"]) == 1

        assert dispatch(["show-ref", "--heads"]) == 0
        output = capsys.readouterr().out
        assert "refs/heads/main" in output
        assert "refs/heads/side" in output

        assert dispatch(["show-ref", "--verify", "refs/heads/missing"]) == 1
        assert capsys.readouterr().out == ""

    def test_unknown_command_falls_back_to_legacy_cli(self) -> None:
        assert dispatch(["status"]) is None
