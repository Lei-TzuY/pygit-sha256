"""Phase 138 tests: rev-list Unix timestamp age filtering."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.rev_list_age_filter import rev_list_age_filter


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
    blob = repo.store.write(BlobObject(b"age-filter\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))

    root = _commit(repo, tree, [], "root", 100)
    base = _commit(repo, tree, [root], "base", 200)
    side = _commit(repo, tree, [base], "side", 250)
    main = _commit(repo, tree, [base], "main", 300)
    merge = _commit(repo, tree, [main, side], "merge", 400)

    repo.refs.set_branch("base", base)
    repo.refs.set_branch("side", side)
    repo.refs.set_branch("main", merge)
    repo.refs.set_head_symbolic("main")
    return repo, {
        "root": root,
        "base": base,
        "side": side,
        "main": main,
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


def test_age_api_uses_strict_native_timestamp_bounds(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    entries = rev_list_age_filter(
        repo,
        ["main"],
        topo_order=True,
        max_age=200,
        min_age=400,
    )

    assert [entry.oid for entry in entries] == [h["main"], h["side"]]


def test_max_age_keeps_only_commits_strictly_newer_than_timestamp(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--topo-order", "--max-age=250", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["merge"], h["main"]]


def test_min_age_keeps_only_commits_strictly_older_than_timestamp(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--topo-order", "--min-age=250", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["base"], h["root"]]


def test_age_filters_run_before_skip_and_max_count(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(
        repo,
        "rev-list",
        "--topo-order",
        "--max-age=100",
        "--skip",
        "1",
        "-n",
        "2",
        "main",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["main"], h["side"]]


def test_age_filter_composes_with_oldest_count(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(
        repo,
        "rev-list",
        "--topo-order",
        "--max-age=100",
        "--max-count-oldest=2",
        "main",
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["side"], h["base"]]


def test_age_filter_composes_with_parent_count_filter(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--topo-order", "--no-merges", "--max-age=200", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["main"], h["side"]]


def test_age_filter_children_keep_prelimit_child_metadata(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--children", "--topo-order", "--min-age=300", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        f"{h['side']} {h['merge']}",
        f"{h['base']} {h['main']} {h['side']}",
        f"{h['root']} {h['base']}",
    ]


def test_age_filter_boundary_advertises_filtered_direct_parents(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--boundary", "--topo-order", "--max-age=250", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        h["merge"],
        h["main"],
        f"-{h['side']}",
        f"-{h['base']}",
    ]


def test_age_filter_count_counts_only_filtered_commit_records(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--count", "--max-age=200", "--min-age=400", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"2\n"


def test_age_filter_objects_expand_only_selected_commit_closure(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--objects", "--topo-order", "--max-age=300", "main")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    assert lines[0] == h["merge"]
    assert h["main"] not in lines
    assert h["side"] not in lines
    assert any(line.startswith(h["tree"]) for line in lines[1:])
    assert any(line == f"{h['blob']} file.txt" for line in lines[1:])


def test_age_filter_reverse_is_final_presentation_transform(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "rev-list", "--reverse", "--topo-order", "--max-age=200", "--min-age=400", "main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [h["side"], h["main"]]


def test_negative_age_timestamp_is_rejected(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--max-age=-1", "main")

    assert result.returncode != 0
    assert b"non-negative Unix timestamp" in result.stderr


def test_installed_help_lists_phase138_age_options(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--max-age" in result.stdout
    assert b"--min-age" in result.stdout
