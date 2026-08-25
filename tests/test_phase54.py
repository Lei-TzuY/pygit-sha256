"""Phase 54 tests: packed refs and transparent ref backend integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository
from pygit.command import dispatch
from pygit.name_rev import name_revisions
from pygit.objects import CommitObject, Identity, TagObject, TreeObject
from pygit.packed_refs import pack_refs, read_packed_refs
from pygit.plumbing import list_refs, verify_ref
from pygit.ref_transaction import set_symbolic_ref, update_ref


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, parents: list[str], message: str, timestamp: int) -> str:
    tree = repo.store.write(TreeObject())
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _history(repo: Repository) -> tuple[str, str]:
    root = _commit(repo, [], "root", 1)
    tip = _commit(repo, [root], "tip", 2)
    repo.refs.set_branch("main", tip)
    return root, tip


class TestPackedRefBackend:
    def test_default_pack_only_tags_and_prunes_loose_tag(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        repo.refs.set_tag("v1", tip)

        packed = pack_refs(repo)

        assert [record.refname for record in packed] == ["refs/tags/v1"]
        assert not (repo.pygit_dir / "refs" / "tags" / "v1").exists()
        assert (repo.pygit_dir / "refs" / "heads" / "main").exists()
        assert repo.refs.get_tag("v1") == tip
        assert repo.refs.list_tags() == ["v1"]
        assert repo.refs.resolve("v1") == tip
        assert verify_ref(repo, "refs/tags/v1") == (tip, "refs/tags/v1")

        refs = dict((name, oid) for oid, name in list_refs(repo, tags=True))
        assert refs == {"refs/tags/v1": tip}

    def test_all_packs_branch_remote_and_keeps_head_resolvable(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip = _history(repo)
        repo.refs.set_remote("origin", "main", tip)
        repo.refs.set_tag("base", root)

        pack_refs(repo, all_refs=True)

        assert not (repo.pygit_dir / "refs" / "heads" / "main").exists()
        assert not (repo.pygit_dir / "refs" / "remotes" / "origin" / "main").exists()
        assert repo.refs.resolve_head() == tip
        assert repo.refs.get_branch("main") == tip
        assert repo.refs.get_remote("origin", "main") == tip
        assert repo.refs.list_branches() == ["main"]
        assert repo.refs.list_remotes() == ["origin/main"]

        all_refs = {name: oid for oid, name in list_refs(repo)}
        assert all_refs["refs/heads/main"] == tip
        assert all_refs["refs/remotes/origin/main"] == tip
        assert all_refs["refs/tags/base"] == root

        named = name_revisions(repo, [root], tags_only=True)
        assert named[0].name == "tags/base"

    def test_annotated_tag_records_peeled_object_and_show_ref_dereferences(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        tag_oid = repo.store.write(
            TagObject(
                target_sha=tip,
                target_type=b"commit",
                tag_name="v2",
                tagger=Identity("Tagger", "tagger@example.com", 3, "+0000"),
                message="release",
            )
        )
        repo.refs.set_tag("v2", tag_oid)
        pack_refs(repo)

        record = read_packed_refs(repo.pygit_dir)["refs/tags/v2"]
        assert record.oid == tag_oid
        assert record.peeled_oid == tip

        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()
        assert dispatch(["show-ref", "--tags", "--dereference"]) == 0
        output = capsys.readouterr().out
        assert f"{tag_oid} refs/tags/v2" in output
        assert f"{tip} refs/tags/v2^{{}}" in output

    def test_loose_update_shadows_packed_value_then_repack_refreshes_it(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip = _history(repo)
        pack_refs(repo, all_refs=True)
        newer = _commit(repo, [tip], "newer", 3)

        repo.refs.set_branch("main", newer)
        assert repo.refs.get_branch("main") == newer
        assert read_packed_refs(repo.pygit_dir)["refs/heads/main"].oid == tip
        assert dict((name, oid) for oid, name in list_refs(repo))["refs/heads/main"] == newer

        pack_refs(repo, all_refs=True)
        assert not (repo.pygit_dir / "refs" / "heads" / "main").exists()
        assert read_packed_refs(repo.pygit_dir)["refs/heads/main"].oid == newer
        assert repo.refs.resolve_head() == newer
        assert root != newer

    def test_no_prune_keeps_loose_ref_but_writes_packed_copy(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        repo.refs.set_tag("v1", tip)

        pack_refs(repo, prune=False)

        assert (repo.pygit_dir / "refs" / "tags" / "v1").is_file()
        assert read_packed_refs(repo.pygit_dir)["refs/tags/v1"].oid == tip
        assert repo.refs.get_tag("v1") == tip

    def test_symbolic_refs_are_not_packed_and_remain_queryable(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        set_symbolic_ref(repo, "refs/aliases/current", "refs/heads/main")

        pack_refs(repo, all_refs=True)

        alias = repo.pygit_dir / "refs" / "aliases" / "current"
        assert alias.read_text(encoding="utf-8").strip() == "ref: refs/heads/main"
        assert "refs/aliases/current" not in read_packed_refs(repo.pygit_dir)
        assert repo.refs.resolve("refs/aliases/current") == tip
        refs = {name: oid for oid, name in list_refs(repo)}
        assert refs["refs/aliases/current"] == tip


class TestPackedRefTransactions:
    def test_compare_and_swap_updates_packed_branch_through_loose_shadow(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        pack_refs(repo, all_refs=True)
        newer = _commit(repo, [tip], "newer", 3)

        update_ref(repo, "refs/heads/main", newer, old_oid=tip)

        loose = repo.pygit_dir / "refs" / "heads" / "main"
        assert loose.read_text(encoding="utf-8").strip() == newer
        assert repo.refs.get_branch("main") == newer
        assert read_packed_refs(repo.pygit_dir)["refs/heads/main"].oid == tip

        with pytest.raises(RuntimeError, match="cannot lock ref"):
            update_ref(repo, "refs/heads/main", tip, old_oid="0" * 64)
        assert repo.refs.get_branch("main") == newer

    def test_deleting_packed_ref_removes_backing_value_instead_of_resurrecting(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        repo.refs.set_branch("topic", tip)
        pack_refs(repo, all_refs=True)
        assert repo.refs.get_branch("topic") == tip

        update_ref(repo, "refs/heads/topic", None, old_oid=tip, delete=True)

        assert repo.refs.get_branch("topic") is None
        assert "topic" not in repo.refs.list_branches()
        assert "refs/heads/topic" not in read_packed_refs(repo.pygit_dir)
        assert all(name != "refs/heads/topic" for _, name in list_refs(repo))

    def test_remote_rename_and_delete_rewrite_packed_namespace(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        repo.refs.set_remote("origin", "main", tip)
        repo.refs.set_remote("origin", "dev", tip)
        pack_refs(repo, all_refs=True)

        repo.refs.rename_remote("origin", "upstream")
        records = read_packed_refs(repo.pygit_dir)
        assert "refs/remotes/origin/main" not in records
        assert records["refs/remotes/upstream/main"].oid == tip
        assert records["refs/remotes/upstream/dev"].oid == tip
        assert repo.refs.list_remotes("upstream") == ["dev", "main"]

        repo.refs.delete_remote("upstream")
        assert repo.refs.list_remotes("upstream") == []
        assert not any(name.startswith("refs/remotes/upstream/") for name in read_packed_refs(repo.pygit_dir))


class TestPackedRefsCLIAndValidation:
    def test_pack_refs_cli_all_and_no_prune(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        _, tip = _history(repo)
        repo.refs.set_tag("v1", tip)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        assert dispatch(["pack-refs", "--all", "--no-prune"]) == 0
        assert capsys.readouterr().out == ""
        assert (repo.pygit_dir / "refs" / "heads" / "main").exists()
        records = read_packed_refs(repo.pygit_dir)
        assert records["refs/heads/main"].oid == tip
        assert records["refs/tags/v1"].oid == tip

    @pytest.mark.parametrize(
        "contents, message",
        [
            ("^" + "1" * 64 + "\n", "orphan peeled object"),
            ("not-an-oid refs/heads/main\n", "invalid object ID"),
            (("1" * 64) + " refs/heads/main\n" + ("2" * 64) + " refs/heads/main\n", "duplicate ref"),
            (("1" * 64) + " refs/heads/bad..name\n", "invalid packed ref name"),
        ],
    )
    def test_malformed_packed_refs_fail_loudly(self, tmp_path: Path, contents: str, message: str) -> None:
        repo = _repo(tmp_path)
        (repo.pygit_dir / "packed-refs").write_text(contents, encoding="utf-8")

        with pytest.raises(RuntimeError, match=message):
            read_packed_refs(repo.pygit_dir)
