"""Phase 135 tests: rev-list excluded boundary commit presentation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_boundary import boundary_children, rev_list_boundary


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
    repo = Repository.init(str(tmp_path / "repo"))
    blob = repo.store.write(BlobObject(b"boundary\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))

    root = _commit(repo, tree, [], "root", 0)
    base = _commit(repo, tree, [root], "base", 1)
    left = _commit(repo, tree, [base], "left", 2)
    right = _commit(repo, tree, [base], "right", 3)
    merge = _commit(repo, tree, [left, right], "merge", 4)

    repo.refs.set_branch("base", base)
    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_branch("main", merge)
    repo.refs.set_head_symbolic("main")
    return repo, {
        "root": root,
        "base": base,
        "left": left,
        "right": right,
        "merge": merge,
        "tree": tree,
        "blob": blob,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_boundary_api_reports_range_parent(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_boundary(repo, ["base..main"], topo_order=True)

    assert [(entry.oid, entry.boundary) for entry in entries] == [
        (h["merge"], False),
        (h["right"], False),
        (h["left"], False),
        (h["base"], True),
    ]


def test_max_count_creates_visible_limit_boundary(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_boundary(repo, ["main"], topo_order=True, max_count=1)

    assert [(entry.oid, entry.boundary) for entry in entries] == [
        (h["merge"], False),
        (h["right"], True),
        (h["left"], True),
    ]


def test_boundary_order_uses_commit_order_not_stored_parent_order(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_boundary(repo, ["main"], max_count=1)

    # merge stores [left, right], but right is newer and Git orders boundary
    # commits by the active revision ordering policy.
    assert [entry.oid for entry in entries] == [h["merge"], h["right"], h["left"]]


def test_reverse_reverses_combined_selected_and_boundary_stream(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_boundary(repo, ["base..main"], topo_order=True, reverse=True)

    assert entries[0].oid == h["base"]
    assert entries[0].boundary is True
    assert entries[-1].oid == h["merge"]


def test_first_parent_limits_boundary_edges(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_boundary(repo, ["main"], first_parent=True, max_count=1)

    assert [(entry.oid, entry.boundary) for entry in entries] == [
        (h["merge"], False),
        (h["left"], True),
    ]


def test_shallow_commit_does_not_advertise_hidden_parent_boundary(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    (repo.pygit_dir / "shallow").write_text(h["left"] + "\n", encoding="utf-8")

    entries = rev_list_boundary(repo, ["left"], topo_order=True)

    assert [(entry.oid, entry.boundary) for entry in entries] == [(h["left"], False)]


def test_boundary_children_reports_visible_child(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    entries = rev_list_boundary(repo, ["base..main"], topo_order=True)

    children = boundary_children(repo, entries)

    assert children[h["base"]] == (h["right"], h["left"])


def test_cli_boundary_formats_range_and_limit_edges(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    ranged = _run(repo, "rev-list", "--boundary", "--topo-order", "base..main")
    limited = _run(repo, "rev-list", "--boundary", "--topo-order", "-n", "1", "main")

    assert ranged.returncode == 0, ranged.stderr.decode()
    assert ranged.stdout.decode().splitlines() == [
        h["merge"],
        h["right"],
        h["left"],
        f"-{h['base']}",
    ]
    assert limited.returncode == 0, limited.stderr.decode()
    assert limited.stdout.decode().splitlines() == [
        h["merge"],
        f"-{h['right']}",
        f"-{h['left']}",
    ]


def test_cli_boundary_composes_with_parents_and_children(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    parents = _run(repo, "rev-list", "--boundary", "--parents", "--topo-order", "base..main")
    children = _run(repo, "rev-list", "--boundary", "--children", "--topo-order", "base..main")

    assert parents.returncode == 0, parents.stderr.decode()
    assert parents.stdout.decode().splitlines()[-1] == f"-{h['base']} {h['root']}"
    assert children.returncode == 0, children.stderr.decode()
    assert children.stdout.decode().splitlines()[-1] == f"-{h['base']} {h['right']} {h['left']}"


def test_cli_boundary_left_right_uses_dash_for_common_boundary(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--left-right", "--boundary", "--topo-order", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        f">{h['right']}",
        f"<{h['left']}",
        f"-{h['base']}",
    ]


def test_cli_boundary_count_includes_boundary_records(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--boundary", "--count", "base..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"4\n"


def test_cli_left_right_boundary_count_matches_native_boundary_accounting(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--left-right", "--boundary", "--count", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"2\t1\n"


def test_cli_objects_boundary_inserts_commit_before_named_tree_objects(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--boundary", "--topo-order", "base..main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[:4] == [h["merge"], h["right"], h["left"], f"-{h['base']}"]
    assert any(line.startswith(h["tree"]) for line in lines[4:])
    assert any(line == f"{h['blob']} file.txt" for line in lines[4:])


def test_cli_objects_reverse_moves_boundary_before_selected_commits(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(
        repo,
        "rev-list",
        "--objects",
        "--boundary",
        "--reverse",
        "--topo-order",
        "base..main",
    )

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[:4] == [f"-{h['base']}", h["left"], h["right"], h["merge"]]


def test_cli_objects_edge_boundary_deduplicates_same_range_edge(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--objects-edge", "--boundary", "--topo-order", "base..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines().count(f"-{h['base']}") == 1


def test_cli_objects_boundary_count_adds_boundary_to_object_count(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    plain = _run(repo, "rev-list", "--objects", "--count", "base..main")
    bounded = _run(repo, "rev-list", "--objects", "--boundary", "--count", "base..main")

    assert plain.returncode == 0
    assert bounded.returncode == 0
    assert int(bounded.stdout) == int(plain.stdout) + 1


def test_installed_help_lists_phase135_boundary_option(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--boundary" in result.stdout
