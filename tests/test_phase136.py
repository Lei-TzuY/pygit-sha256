"""Phase 136 tests: rev-list parent-count selection filters."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_parent_filter import rev_list_parent_filter


def _tree(repo: Repository, name: str) -> tuple[str, str]:
    blob = repo.store.write(BlobObject((name + "\n").encode()))
    tree = repo.store.write(TreeObject([TreeEntry("100644", f"{name}.txt", blob)]))
    return tree, blob


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
    root_tree, root_blob = _tree(repo, "root")
    left_tree, left_blob = _tree(repo, "left")
    right_tree, right_blob = _tree(repo, "right")
    merge_tree, merge_blob = _tree(repo, "merge")
    after_tree, after_blob = _tree(repo, "after")
    octopus_tree, octopus_blob = _tree(repo, "octopus")

    root = _commit(repo, root_tree, [], "root", 0)
    left = _commit(repo, left_tree, [root], "left", 1)
    right = _commit(repo, right_tree, [root], "right", 2)
    merge = _commit(repo, merge_tree, [left, right], "merge", 3)
    after = _commit(repo, after_tree, [merge], "after", 4)
    octopus = _commit(repo, octopus_tree, [after, left, right], "octopus", 5)

    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_branch("merge", merge)
    repo.refs.set_branch("main", octopus)
    repo.refs.set_head_symbolic("main")
    return repo, {
        "root": root,
        "left": left,
        "right": right,
        "merge": merge,
        "after": after,
        "octopus": octopus,
        "root_tree": root_tree,
        "root_blob": root_blob,
        "left_tree": left_tree,
        "left_blob": left_blob,
        "merge_tree": merge_tree,
        "merge_blob": merge_blob,
        "octopus_tree": octopus_tree,
        "octopus_blob": octopus_blob,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_api_filters_by_parent_count_before_limits(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_parent_filter(repo, ["main"], topo_order=True, max_parents=1, max_count=1)

    assert [entry.oid for entry in entries] == [h["after"]]


def test_api_min_parents_three_selects_octopus_merge(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_parent_filter(repo, ["main"], topo_order=True, min_parents=3)

    assert [entry.oid for entry in entries] == [h["octopus"]]


def test_api_rejects_negative_min_parents(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    with pytest.raises(ValueError, match="min-parents"):
        rev_list_parent_filter(repo, ["main"], min_parents=-1)


def test_cli_merges_is_min_parents_two(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    alias = _run(repo, "rev-list", "--topo-order", "--merges", "main")
    explicit = _run(repo, "rev-list", "--topo-order", "--min-parents", "2", "main")

    assert alias.returncode == explicit.returncode == 0
    assert alias.stdout == explicit.stdout
    assert alias.stdout.decode().splitlines() == [h["octopus"], h["merge"]]


def test_cli_no_merges_is_max_parents_one(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    alias = _run(repo, "rev-list", "--topo-order", "--no-merges", "main")
    explicit = _run(repo, "rev-list", "--topo-order", "--max-parents", "1", "main")

    assert alias.returncode == explicit.returncode == 0
    assert alias.stdout == explicit.stdout
    assert h["octopus"].encode() not in alias.stdout
    assert h["merge"].encode() not in alias.stdout
    assert h["after"].encode() in alias.stdout


def test_cli_max_parents_zero_selects_root(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--max-parents", "0", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (h["root"] + "\n").encode()


def test_cli_reset_options_follow_command_line_order(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    reset = _run(repo, "rev-list", "--merges", "--no-min-parents", "--count", "main")
    reapplied = _run(repo, "rev-list", "--no-min-parents", "--merges", "--count", "main")

    assert reset.returncode == reapplied.returncode == 0
    assert reset.stdout == b"6\n"
    assert reapplied.stdout == b"2\n"


def test_cli_negative_max_parents_resets_upper_limit(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--max-parents", "-1", "--count", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"6\n"


def test_cli_first_parent_walk_still_counts_real_merge_parents(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--first-parent", "--merges", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["octopus"], h["merge"]]


def test_cli_shallow_boundary_counts_as_synthetic_root(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    (repo.pygit_dir / "shallow").write_text(h["left"] + "\n", encoding="utf-8")

    result = _run(repo, "rev-list", "--max-parents", "0", "left")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (h["left"] + "\n").encode()


def test_cli_filter_applies_before_skip_and_max_count(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--topo-order", "--no-merges", "--skip", "1", "-n", "1", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().strip() in {h["right"], h["left"]}
    assert result.stdout.decode().strip() != h["after"]


def test_cli_merges_boundary_advertises_filtered_out_direct_parents(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--merges", "--boundary", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[:2] == [h["octopus"], h["merge"]]
    assert f"-{h['after']}" in lines
    assert f"-{h['left']}" in lines
    assert f"-{h['right']}" in lines


def test_cli_children_merges_keeps_pre_filter_direct_child_metadata(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--children", "--merges", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == h["octopus"]
    assert lines[1] == f"{h['merge']} {h['after']}"


def test_cli_parents_merges_keeps_real_parent_list(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--parents", "--merges", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0].startswith(h["octopus"] + " ")
    assert h["after"] in lines[0]
    assert lines[1].startswith(h["merge"] + " ")


def test_cli_objects_expand_only_parent_filtered_commit_closure(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--merges", "--topo-order", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[:2] == [h["octopus"], h["merge"]]
    assert any(line.startswith(h["octopus_tree"]) for line in lines)
    assert any(line == f"{h['octopus_blob']} octopus.txt" for line in lines)
    assert any(line.startswith(h["merge_tree"]) for line in lines)
    assert any(line == f"{h['merge_blob']} merge.txt" for line in lines)
    assert all(not line.startswith(h["root_tree"]) for line in lines)
    assert all(not line.startswith(h["root_blob"]) for line in lines)
    assert all(not line.startswith(h["left_tree"]) for line in lines)
    assert all(not line.startswith(h["left_blob"]) for line in lines)


def test_cli_objects_count_uses_filtered_object_set(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    all_objects = _run(repo, "rev-list", "--objects", "--count", "main")
    merge_objects = _run(repo, "rev-list", "--objects", "--merges", "--count", "main")

    assert all_objects.returncode == merge_objects.returncode == 0
    assert int(merge_objects.stdout) < int(all_objects.stdout)


def test_installed_help_lists_phase136_parent_filters(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    for option in (b"--merges", b"--no-merges", b"--min-parents", b"--max-parents"):
        assert option in result.stdout
