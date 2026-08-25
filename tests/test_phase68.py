"""Phase 68 tests: script-facing rev-list commit-set plumbing."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list import rev_list


def _commit(repo: Repository, tree: str, parents: list[str], name: str, timestamp: int) -> str:
    ident = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=ident,
            committer=ident,
            message=name,
        )
    )


def _graph(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "r"))
    blob = repo.store.write(BlobObject(b"graph\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))

    # Deliberately give the root a newer timestamp than its children.  Default
    # date ordering may therefore put it first, while --topo-order must not.
    root = _commit(repo, tree, [], "root", 1000)
    left = _commit(repo, tree, [root], "left", 10)
    right = _commit(repo, tree, [root], "right", 20)
    merge = _commit(repo, tree, [left, right], "merge", 30)
    tip = _commit(repo, tree, [merge], "tip", 40)
    orphan = _commit(repo, tree, [], "orphan", 50)

    repo.refs.set_branch("main", tip)
    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_branch("orphan", orphan)
    repo.refs.set_head_symbolic("main")
    # A non-commit ref must not make --all attempt to walk a blob as a commit.
    repo.refs.set_tag("blob-target", blob)

    return repo, {
        "blob": blob,
        "tree": tree,
        "root": root,
        "left": left,
        "right": right,
        "merge": merge,
        "tip": tip,
        "orphan": orphan,
    }


def _oids(entries) -> list[str]:
    return [entry.oid for entry in entries]


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_positive_negative_and_two_dot_revision_sets(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    explicit = set(_oids(rev_list(repo, ["HEAD", "^left"])))
    ranged = set(_oids(rev_list(repo, ["left..HEAD"])))

    assert explicit == {h["tip"], h["merge"], h["right"]}
    assert ranged == explicit


def test_multiple_positive_tips_and_all_refs(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    two_tips = set(_oids(rev_list(repo, ["left", "right"])))
    every_ref = set(_oids(rev_list(repo, all_refs=True)))

    assert two_tips == {h["left"], h["right"], h["root"]}
    assert every_ref == {h["tip"], h["merge"], h["left"], h["right"], h["root"], h["orphan"]}
    assert h["blob"] not in every_ref


def test_symmetric_range_and_left_right_markers(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    plain = rev_list(repo, ["left...right"], topo_order=True)
    marked = rev_list(repo, ["left...right"], topo_order=True, left_right=True)

    assert set(_oids(plain)) == {h["left"], h["right"]}
    assert {entry.oid: entry.side for entry in marked} == {
        h["left"]: "<",
        h["right"]: ">",
    }
    with pytest.raises(ValueError, match="left-right"):
        rev_list(repo, ["HEAD"], left_right=True)


def test_first_parent_and_shallow_boundary_limit_walks(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    first_parent = set(_oids(rev_list(repo, ["HEAD"], first_parent=True)))
    assert first_parent == {h["tip"], h["merge"], h["left"], h["root"]}
    assert h["right"] not in first_parent

    (repo.pygit_dir / "shallow").write_text(h["merge"] + "\n", encoding="utf-8")
    shallow = set(_oids(rev_list(repo, ["HEAD"])))
    assert shallow == {h["tip"], h["merge"]}


def test_topological_order_beats_clock_skew(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    by_date = _oids(rev_list(repo, ["left"]))
    topo = _oids(rev_list(repo, ["left"], topo_order=True))

    assert by_date[0] == h["root"]
    assert topo == [h["left"], h["root"]]


def test_skip_limit_reverse_and_count_selection(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    baseline = _oids(rev_list(repo, ["HEAD"], topo_order=True))
    limited = _oids(rev_list(repo, ["HEAD"], topo_order=True, skip=1, max_count=2))
    reversed_limited = _oids(
        rev_list(repo, ["HEAD"], topo_order=True, skip=1, max_count=2, reverse=True)
    )

    assert limited == baseline[1:3]
    assert reversed_limited == list(reversed(baseline[1:3]))
    with pytest.raises(ValueError, match="skip"):
        rev_list(repo, ["HEAD"], skip=-1)
    with pytest.raises(ValueError, match="max-count"):
        rev_list(repo, ["HEAD"], max_count=-1)


def test_cli_routes_advanced_rev_list_before_legacy_parser(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    ranged = _run(repo, "rev-list", "--topo-order", "left..HEAD")
    marked = _run(repo, "rev-list", "--left-right", "left...right")
    counted = _run(repo, "rev-list", "--count", "--all")
    limited = _run(repo, "rev-list", "--topo-order", "--skip", "1", "-n", "2", "HEAD")

    assert ranged.returncode == 0, ranged.stderr.decode()
    assert set(ranged.stdout.decode().splitlines()) == {h["tip"], h["merge"], h["right"]}
    assert marked.returncode == 0, marked.stderr.decode()
    assert set(marked.stdout.decode().splitlines()) == {f"<{h['left']}", f">{h['right']}"}
    assert counted.returncode == 0, counted.stderr.decode()
    assert counted.stdout == b"6\n"
    assert limited.returncode == 0, limited.stderr.decode()
    assert limited.stdout.decode().splitlines() == _oids(
        rev_list(repo, ["HEAD"], topo_order=True, skip=1, max_count=2)
    )
