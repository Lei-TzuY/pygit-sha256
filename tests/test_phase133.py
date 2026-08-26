"""Phase 133 tests: rev-list parent metadata and --parents presentation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_parents import parent_oids, rev_list_parents


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
    blob = repo.store.write(BlobObject(b"parents\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))

    root = _commit(repo, tree, [], "root", 0)
    left = _commit(repo, tree, [root], "left", 1)
    right = _commit(repo, tree, [root], "right", 2)
    merge = _commit(repo, tree, [left, right], "merge", 3)

    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_branch("main", merge)
    repo.refs.set_head_symbolic("main")
    return repo, {"root": root, "left": left, "right": right, "merge": merge, "tree": tree, "blob": blob}


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_parent_api_preserves_raw_parents_outside_selected_range(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_parents(repo, ["left..main"], topo_order=True)

    assert [(entry.oid, entry.parents) for entry in entries] == [
        (h["merge"], (h["left"], h["right"])),
        (h["right"], (h["root"],)),
    ]


def test_first_parent_changes_walk_but_not_merge_parent_display(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_parents(repo, ["main"], first_parent=True, topo_order=True)

    assert [entry.oid for entry in entries] == [h["merge"], h["left"], h["root"]]
    assert entries[0].parents == (h["left"], h["right"])


def test_shallow_boundary_is_presented_as_root(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    (repo.pygit_dir / "shallow").write_text(h["left"] + "\n", encoding="utf-8")

    assert parent_oids(repo, h["left"]) == ()
    entries = rev_list_parents(repo, ["left"], topo_order=True)
    assert [(entry.oid, entry.parents) for entry in entries] == [(h["left"], ())]


def test_cli_parents_preserves_excluded_boundary_parents(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--parents", "--topo-order", "left..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout.decode().splitlines() == [
        f"{h['merge']} {h['left']} {h['right']}",
        f"{h['right']} {h['root']}",
    ]


def test_cli_first_parent_still_prints_all_merge_parents(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--parents", "--first-parent", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == f"{h['merge']} {h['left']} {h['right']}"
    assert [line.split()[0] for line in lines] == [h["merge"], h["left"], h["root"]]


def test_cli_parents_composes_with_left_right_markers(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--parents", "--left-right", "--topo-order", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        f">{h['right']} {h['root']}",
        f"<{h['left']} {h['root']}",
    ]


def test_cli_objects_parents_formats_commit_records_only(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--parents", "-n", "1", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == f"{h['merge']} {h['left']} {h['right']}"
    assert h["tree"] in lines[1].split()[0]
    assert any(line == f"{h['blob']} file.txt" for line in lines)


def test_cli_count_ignores_parent_presentation(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    plain = _run(repo, "rev-list", "--count", "main")
    parents = _run(repo, "rev-list", "--parents", "--count", "main")

    assert plain.returncode == 0
    assert parents.returncode == 0
    assert parents.stdout == plain.stdout == b"4\n"


def test_installed_help_lists_phase133_parents_option(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--parents" in result.stdout
