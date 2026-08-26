"""Phase 134 tests: rev-list child metadata and --children presentation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_children import rev_list_children


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
    blob = repo.store.write(BlobObject(b"children\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))

    root = _commit(repo, tree, [], "root", 0)
    left = _commit(repo, tree, [root], "left", 1)
    right = _commit(repo, tree, [root], "right", 2)
    merge = _commit(repo, tree, [left, right], "merge", 3)

    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_branch("main", merge)
    repo.refs.set_head_symbolic("main")
    return repo, {
        "root": root,
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


def test_child_api_reports_selected_graph_edges(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_children(repo, ["main"], topo_order=True)

    assert [(entry.oid, entry.children) for entry in entries] == [
        (h["merge"], ()),
        (h["right"], (h["merge"],)),
        (h["left"], (h["merge"],)),
        (h["root"], (h["right"], h["left"])),
    ]


def test_child_api_excludes_edges_from_uninteresting_commits(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_children(repo, ["left..main"], topo_order=True)

    assert [(entry.oid, entry.children) for entry in entries] == [
        (h["merge"], ()),
        (h["right"], (h["merge"],)),
    ]


def test_skip_preserves_child_that_was_removed_from_output(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_children(repo, ["main"], topo_order=True, skip=1)

    assert entries[0].oid == h["right"]
    assert entries[0].children == (h["merge"],)


def test_reverse_changes_record_order_not_child_order(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_children(repo, ["main"], topo_order=True, reverse=True)

    assert entries[0].oid == h["root"]
    assert entries[0].children == (h["right"], h["left"])


def test_first_parent_restricts_child_edges_to_walked_parent_links(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_children(repo, ["main"], first_parent=True, topo_order=True)

    assert [(entry.oid, entry.children) for entry in entries] == [
        (h["merge"], ()),
        (h["left"], (h["merge"],)),
        (h["root"], (h["left"],)),
    ]


def test_shallow_boundary_does_not_create_child_edge_to_hidden_parent(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    (repo.pygit_dir / "shallow").write_text(h["left"] + "\n", encoding="utf-8")

    entries = rev_list_children(repo, ["left"], topo_order=True)

    assert [(entry.oid, entry.children) for entry in entries] == [(h["left"], ())]


def test_cli_children_formats_merge_graph(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--children", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout.decode().splitlines() == [
        h["merge"],
        f"{h['right']} {h['merge']}",
        f"{h['left']} {h['merge']}",
        f"{h['root']} {h['right']} {h['left']}",
    ]


def test_cli_children_skip_matches_native_prelimit_metadata(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--children", "--topo-order", "--skip", "1", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines()[0] == f"{h['right']} {h['merge']}"


def test_cli_children_composes_with_left_right_marker(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--children", "--left-right", "--topo-order", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [f">{h['right']}", f"<{h['left']}"]


def test_cli_objects_children_formats_commit_records_only(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--children", "--skip", "1", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == f"{h['right']} {h['merge']}"
    assert any(line == f"{h['blob']} file.txt" for line in lines)
    assert any(line.startswith(h["tree"]) for line in lines)


def test_cli_count_ignores_child_presentation(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    plain = _run(repo, "rev-list", "--count", "main")
    children = _run(repo, "rev-list", "--children", "--count", "main")

    assert plain.returncode == 0
    assert children.returncode == 0
    assert children.stdout == plain.stdout == b"4\n"


def test_cli_rejects_parents_and_children_together(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--parents", "--children", "main")

    assert result.returncode != 0
    assert b"not allowed with argument" in result.stderr


def test_installed_help_lists_phase134_children_option(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--children" in result.stdout
