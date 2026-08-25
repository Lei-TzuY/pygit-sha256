"""Phase 53 tests: symbolic commit naming with name-rev."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.command import dispatch
from pygit.name_rev import name_all, name_revisions
from pygit.objects import CommitObject, Identity, TagObject, TreeObject
from pygit.ref_transaction import set_symbolic_ref


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


def _linear(repo: Repository) -> tuple[str, str, str]:
    root = _commit(repo, [], "root", 1)
    middle = _commit(repo, [root], "middle", 2)
    tip = _commit(repo, [middle], "tip", 3)
    repo.refs.set_branch("main", tip)
    return root, middle, tip


class TestNameRevAPI:
    def test_linear_first_parent_names_are_compacted(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, middle, tip = _linear(repo)

        records = name_revisions(repo, [tip, middle, root])
        assert [record.name for record in records] == ["main", "main~1", "main~2"]

    def test_merge_second_parent_uses_caret_notation(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root = _commit(repo, [], "root", 1)
        left = _commit(repo, [root], "left", 2)
        right = _commit(repo, [root], "right", 3)
        merge = _commit(repo, [left, right], "merge", 4)
        repo.refs.set_branch("main", merge)

        assert name_revisions(repo, [right])[0].name == "main^2"
        assert name_revisions(repo, [root])[0].name == "main~2"

    def test_exact_annotated_tag_wins_and_is_marked_as_peeled(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, _, tip = _linear(repo)
        tag_oid = repo.store.write(
            TagObject(
                target_sha=tip,
                target_type=b"commit",
                tag_name="v2",
                tagger=Identity("Tagger", "tagger@example.com", 4, "+0000"),
                message="release",
            )
        )
        repo.refs.set_tag("v2", tag_oid)

        assert name_revisions(repo, [tip])[0].name == "tags/v2^0"
        assert name_revisions(repo, [root], tags_only=True)[0].name == "tags/v2^0~2"

    def test_ref_filters_and_lightweight_tags(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, middle, tip = _linear(repo)
        repo.refs.set_tag("stable", middle)
        repo.refs.set_branch("topic", root)

        only_tags = name_revisions(repo, [root, middle], tags_only=True)
        assert [record.name for record in only_tags] == ["tags/stable~1", "tags/stable"]

        main_only = name_revisions(repo, [root], ref_patterns=["refs/heads/main"])
        assert main_only[0].name == "main~2"

        topic_only = name_revisions(repo, [root], ref_patterns=["topic"])
        assert topic_only[0].name == "topic"
        assert tip != middle

    def test_shallow_boundary_stops_name_propagation(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, middle, tip = _linear(repo)
        (repo.pygit_dir / "shallow").write_text(f"{middle}\n", encoding="utf-8")

        result = name_revisions(repo, [root, middle, tip])
        assert result[0].name is None
        assert result[1].name == "main~1"
        assert result[2].name == "main"

    def test_symbolic_and_non_commit_refs_do_not_break_query(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, _, tip = _linear(repo)
        set_symbolic_ref(repo, "refs/aliases/current", "refs/heads/main")
        tree_oid = repo.store.write(TreeObject())
        repo.refs._path_under(repo.refs.refs_dir, "misc/tree").parent.mkdir(parents=True, exist_ok=True)
        repo.refs._path_under(repo.refs.refs_dir, "misc/tree").write_text(tree_oid + "\n", encoding="utf-8")

        result = name_revisions(repo, [root, tip])
        assert result[0].name == "main~2"
        assert result[1].name == "main"

    def test_name_all_returns_every_reachable_named_commit(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, middle, tip = _linear(repo)
        records = name_all(repo)
        by_oid = {record.oid: record.name for record in records}
        assert by_oid == {root: "main~2", middle: "main~1", tip: "main"}


class TestNameRevCLI:
    def test_name_only_all_and_always_fallback(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        root, middle, tip = _linear(repo)
        orphan = _commit(repo, [], "orphan", 9)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        assert dispatch(["name-rev", "--name-only", middle]) == 0
        assert capsys.readouterr().out.strip() == "main~1"

        assert dispatch(["name-rev", "--always", orphan]) == 0
        assert capsys.readouterr().out.strip() == f"{orphan} {orphan[:12]}"

        assert dispatch(["name-rev", "--all"]) == 0
        output = capsys.readouterr().out
        assert f"{tip} main" in output
        assert f"{root} main~2" in output

    def test_no_undefined_returns_failure_without_mutation(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _repo(tmp_path)
        _, _, tip = _linear(repo)
        orphan = _commit(repo, [], "orphan", 9)
        monkeypatch.chdir(repo.worktree)
        before = repo.refs.get_branch("main")
        capsys.readouterr()

        assert dispatch(["name-rev", "--no-undefined", orphan]) == 1
        assert "cannot describe commit" in capsys.readouterr().err
        assert repo.refs.get_branch("main") == before == tip
