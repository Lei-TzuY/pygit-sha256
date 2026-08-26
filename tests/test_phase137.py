"""Phase 137 tests: rev-list oldest-N commit limiting."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_oldest import rev_list_oldest, rev_list_oldest_children


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


def _linear(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "repo"))
    blob = repo.store.write(BlobObject(b"oldest\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    c1 = _commit(repo, tree, [], "c1", 1)
    c2 = _commit(repo, tree, [c1], "c2", 2)
    c3 = _commit(repo, tree, [c2], "c3", 3)
    c4 = _commit(repo, tree, [c3], "c4", 4)
    c5 = _commit(repo, tree, [c4], "c5", 5)
    repo.refs.set_branch("main", c5)
    repo.refs.set_head_symbolic("main")
    return repo, {"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5, "tree": tree, "blob": blob}


def _fork(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "fork"))
    blob = repo.store.write(BlobObject(b"fork\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    root = _commit(repo, tree, [], "root", 1)
    left = _commit(repo, tree, [root], "left", 2)
    right = _commit(repo, tree, [root], "right", 3)
    merge = _commit(repo, tree, [left, right], "merge", 4)
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


def test_oldest_api_selects_tail_of_normal_order(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    entries = rev_list_oldest(repo, ["main"], max_count_oldest=2)

    assert [entry.oid for entry in entries] == [h["c2"], h["c1"]]


def test_oldest_api_reverse_reverses_selected_tail_only(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    entries = rev_list_oldest(repo, ["main"], max_count_oldest=2, reverse=True)

    assert [entry.oid for entry in entries] == [h["c1"], h["c2"]]


def test_oldest_zero_selects_no_commits(tmp_path: Path) -> None:
    repo, _ = _linear(tmp_path)

    assert rev_list_oldest(repo, ["main"], max_count_oldest=0) == ()


def test_oldest_larger_than_history_is_noop(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    entries = rev_list_oldest(repo, ["main"], max_count_oldest=1000)

    assert [entry.oid for entry in entries] == [h["c5"], h["c4"], h["c3"], h["c2"], h["c1"]]


def test_oldest_children_keep_prelimit_child_metadata(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    entries = rev_list_oldest_children(repo, ["main"], max_count_oldest=1)

    assert len(entries) == 1
    assert entries[0].oid == h["c1"]
    assert entries[0].children == (h["c2"],)


def test_cli_max_count_oldest_matches_last_plain_records(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    result = _run(repo, "rev-list", "--max-count-oldest", "3", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout.decode().splitlines() == [h["c3"], h["c2"], h["c1"]]


def test_cli_max_count_oldest_reverse_matches_oldest_first(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    result = _run(repo, "rev-list", "--reverse", "--max-count-oldest=3", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["c1"], h["c2"], h["c3"]]


def test_cli_rejects_oldest_with_max_count(tmp_path: Path) -> None:
    repo, _ = _linear(tmp_path)

    result = _run(repo, "rev-list", "--max-count-oldest=2", "--max-count=2", "main")

    assert result.returncode != 0
    assert b"cannot be used together" in result.stderr


def test_cli_rejects_oldest_with_skip(tmp_path: Path) -> None:
    repo, _ = _linear(tmp_path)

    result = _run(repo, "rev-list", "--max-count-oldest=2", "--skip=1", "main")

    assert result.returncode != 0
    assert b"cannot be used together" in result.stderr


def test_cli_rejects_negative_oldest_count(tmp_path: Path) -> None:
    repo, _ = _linear(tmp_path)

    result = _run(repo, "rev-list", "--max-count-oldest=-1", "main")

    assert result.returncode != 0
    assert b"must be non-negative" in result.stderr


def test_cli_parent_filter_happens_before_oldest_slice(tmp_path: Path) -> None:
    repo, h = _fork(tmp_path)

    result = _run(repo, "rev-list", "--no-merges", "--topo-order", "--max-count-oldest=2", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["left"], h["root"]]


def test_cli_left_right_filter_happens_before_oldest_slice(tmp_path: Path) -> None:
    repo, h = _fork(tmp_path)

    result = _run(repo, "rev-list", "--left-right", "--max-count-oldest=1", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [f"<{h['left']}"]


def test_cli_left_right_count_counts_selected_oldest_side(tmp_path: Path) -> None:
    repo, _ = _fork(tmp_path)

    result = _run(repo, "rev-list", "--left-right", "--count", "--max-count-oldest=1", "left...right")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"1\t0\n"


def test_cli_oldest_boundary_reports_range_parent(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    result = _run(repo, "rev-list", "--boundary", "--max-count-oldest=1", "c3..main")

    # c3..main contains c5,c4; oldest one is c4 and c3 is its excluded parent.
    repo.refs.set_branch("c3", h["c3"])
    result = _run(repo, "rev-list", "--boundary", "--max-count-oldest=1", "c3..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["c4"], f"-{h['c3']}"]


def test_cli_oldest_children_preserve_hidden_newer_child(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    result = _run(repo, "rev-list", "--children", "--max-count-oldest=1", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [f"{h['c1']} {h['c2']}"]


def test_cli_oldest_objects_expand_only_selected_commit_closure(tmp_path: Path) -> None:
    repo, h = _linear(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--max-count-oldest=1", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == h["c1"]
    assert h["c2"] not in lines
    assert any(line.startswith(h["tree"]) for line in lines[1:])
    assert any(line == f"{h['blob']} file.txt" for line in lines[1:])


def test_cli_oldest_objects_count_matches_selected_closure(tmp_path: Path) -> None:
    repo, _ = _linear(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--count", "--max-count-oldest=1", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"3\n"


def test_installed_help_lists_max_count_oldest(tmp_path: Path) -> None:
    repo, _ = _linear(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--max-count-oldest" in result.stdout
