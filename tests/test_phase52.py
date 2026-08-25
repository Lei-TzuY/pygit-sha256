"""Phase 52 tests: multi-commit merge-base graph modes."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.command import dispatch
from pygit.graph_query import independent_commits, merge_bases_many, octopus_merge_bases
from pygit.objects import CommitObject, TagObject, TreeObject


def _repo(tmp_path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(tmp_path / "r"))
    tree = repo.store.write(TreeObject())
    return repo, tree


def _commit(repo: Repository, tree: str, parents: list[str], label: str) -> str:
    return repo.store.write(CommitObject(tree=tree, parents=parents, message=label))


def _multi_graph(tmp_path: Path):
    repo, tree = _repo(tmp_path)
    root = _commit(repo, tree, [], "root")
    a = _commit(repo, tree, [root], "a")
    b = _commit(repo, tree, [root], "b")
    c = _commit(repo, tree, [a], "c")
    d = _commit(repo, tree, [b], "d")
    return repo, root, a, b, c, d


class TestMultiCommitMergeBase:
    def test_default_multi_uses_hypothetical_merge_semantics(self, tmp_path: Path) -> None:
        repo, root, a, _, c, d = _multi_graph(tmp_path)

        # Default multi-commit mode compares C with a hypothetical merge of A+D.
        assert merge_bases_many(repo, [c, a, d]) == [a]
        # Octopus mode requires ancestry shared by C, A, and D themselves.
        assert octopus_merge_bases(repo, [c, a, d]) == [root]

    def test_independent_discards_reachable_inputs_and_deduplicates(self, tmp_path: Path) -> None:
        repo, root, a, _, c, d = _multi_graph(tmp_path)

        assert independent_commits(repo, [root, a, c, d, c]) == [c, d]

    def test_annotated_tags_and_shallow_boundaries_are_respected(self, tmp_path: Path) -> None:
        repo, root, a, _, c, d = _multi_graph(tmp_path)
        tag = TagObject(
            target_sha=d,
            target_type=b"commit",
            tag_name="v-d",
            message="annotated d",
        )
        repo.refs.set_tag("v-d", repo.store.write(tag))

        assert octopus_merge_bases(repo, [c, a, "v-d"]) == [root]

        # Once A is declared shallow, neither A nor C may walk through it to root.
        (repo.pygit_dir / "shallow").write_text(f"{a}\n", encoding="utf-8")
        assert octopus_merge_bases(repo, [c, a, "v-d"]) == []

    def test_criss_cross_history_has_two_best_bases(self, tmp_path: Path) -> None:
        repo, tree = _repo(tmp_path)
        root = _commit(repo, tree, [], "root")
        left = _commit(repo, tree, [root], "left")
        right = _commit(repo, tree, [root], "right")
        merge_left = _commit(repo, tree, [left, right], "merge-left")
        merge_right = _commit(repo, tree, [right, left], "merge-right")

        bases = merge_bases_many(repo, [merge_left, merge_right])
        assert set(bases) == {left, right}
        assert len(bases) == 2


class TestPhase52CLI:
    def test_cli_octopus_independent_and_all(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo, root, a, _, c, d = _multi_graph(tmp_path)
        repo.refs.set_branch("a", a)
        repo.refs.set_branch("c", c)
        repo.refs.set_branch("d", d)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        assert dispatch(["merge-base", "c", "a", "d"]) == 0
        assert capsys.readouterr().out.strip() == a

        assert dispatch(["merge-base", "--octopus", "c", "a", "d"]) == 0
        assert capsys.readouterr().out.strip() == root

        assert dispatch(["merge-base", "--independent", "a", "c", "d"]) == 0
        assert capsys.readouterr().out.splitlines() == [c, d]

    def test_cli_all_prints_both_criss_cross_bases(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo, tree = _repo(tmp_path)
        root = _commit(repo, tree, [], "root")
        left = _commit(repo, tree, [root], "left")
        right = _commit(repo, tree, [root], "right")
        merge_left = _commit(repo, tree, [left, right], "merge-left")
        merge_right = _commit(repo, tree, [right, left], "merge-right")
        repo.refs.set_branch("merge-left", merge_left)
        repo.refs.set_branch("merge-right", merge_right)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()

        assert dispatch(["merge-base", "--all", "merge-left", "merge-right"]) == 0
        assert set(capsys.readouterr().out.splitlines()) == {left, right}

    def test_pairwise_is_ancestor_still_works(self, tmp_path: Path, monkeypatch) -> None:
        repo, _, a, _, c, d = _multi_graph(tmp_path)
        repo.refs.set_branch("a", a)
        repo.refs.set_branch("c", c)
        repo.refs.set_branch("d", d)
        monkeypatch.chdir(repo.worktree)

        assert dispatch(["merge-base", "--is-ancestor", "a", "c"]) == 0
        assert dispatch(["merge-base", "--is-ancestor", "c", "d"]) == 1
