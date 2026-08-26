"""Phase 141 tests: ``rev-list --disk-usage[=human]`` storage accounting."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.cat_file import object_disk_size
from pygit.count_objects_cli import _human_size
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.pack import PackWriter
from pygit.rev_list_object_names import rev_list_named_objects


def _commit(repo: Repository, tree: str, parents: list[str], name: str, timestamp: int) -> str:
    ident = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=ident,
            committer=ident,
            message=name + "\n",
        )
    )


def _graph(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = Repository.init(str(tmp_path / "repo"))

    blob1 = repo.store.write(BlobObject(b"one\n"))
    tree1 = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob1)]))
    root = _commit(repo, tree1, [], "root", 1)

    blob2 = repo.store.write(BlobObject(b"one\ntwo\n"))
    tree2 = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob2)]))
    middle = _commit(repo, tree2, [root], "middle", 2)

    blob3 = repo.store.write(BlobObject(b"one\ntwo\nthree\n"))
    tree3 = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob3)]))
    tip = _commit(repo, tree3, [middle], "tip", 3)

    repo.refs.set_branch("root", root)
    repo.refs.set_branch("middle", middle)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    return repo, {
        "root": root,
        "middle": middle,
        "tip": tip,
        "blob1": blob1,
        "blob2": blob2,
        "blob3": blob3,
        "tree1": tree1,
        "tree2": tree2,
        "tree3": tree3,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_plain_disk_usage_sums_selected_commit_storage(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    expected = sum(object_disk_size(repo, h[name]) for name in ("tip", "middle", "root"))

    result = _run(repo, "rev-list", "--disk-usage", "HEAD")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout == f"{expected}\n".encode()


def test_objects_disk_usage_sums_exact_selected_object_stream(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)
    entries = rev_list_named_objects(repo, ["HEAD"])
    expected = sum(object_disk_size(repo, entry.oid) for entry in entries)

    result = _run(repo, "rev-list", "--disk-usage", "--objects", "HEAD")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == f"{expected}\n".encode()


def test_boundary_commit_contributes_to_disk_usage(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    expected = sum(object_disk_size(repo, h[name]) for name in ("tip", "middle", "root"))

    result = _run(repo, "rev-list", "--disk-usage", "--boundary", "root..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == f"{expected}\n".encode()


def test_objects_edge_is_advertised_but_not_charged(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    selected = rev_list_named_objects(repo, ["root..main"])
    expected = sum(object_disk_size(repo, entry.oid) for entry in selected)

    result = _run(repo, "rev-list", "--disk-usage", "--objects-edge", "root..main")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [f"-{h['root']}", str(expected)]


def test_human_disk_usage_uses_existing_binary_size_formatter(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)
    entries = rev_list_named_objects(repo, ["HEAD"])
    expected = sum(object_disk_size(repo, entry.oid) for entry in entries)

    result = _run(repo, "rev-list", "--disk-usage=human", "--objects", "HEAD")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (_human_size(expected) + "\n").encode()


def test_count_plus_disk_usage_matches_native_zero_count_protocol(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    expected = sum(object_disk_size(repo, h[name]) for name in ("tip", "middle", "root"))

    result = _run(repo, "rev-list", "--disk-usage", "--count", "HEAD")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == ["0", str(expected)]


def test_presentation_flags_do_not_change_disk_accounting(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    expected = object_disk_size(repo, h["tip"])

    result = _run(
        repo,
        "rev-list",
        "--disk-usage",
        "--header",
        "--timestamp",
        "--parents",
        "-n",
        "1",
        "HEAD",
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == f"{expected}\n".encode()
    assert b"\x00" not in result.stdout


def test_disk_usage_uses_exact_packed_entry_width(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    tip = h["tip"]
    obj = repo.store.read(tip)
    PackWriter([(tip, obj)]).write_pack_and_idx(repo.store.root / "pack")
    repo.store._path_for(tip).unlink()
    assert not repo.store._path_for(tip).exists()
    expected = object_disk_size(repo, tip)

    result = _run(repo, "rev-list", "--disk-usage", "-n", "1", "HEAD")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == f"{expected}\n".encode()
    assert not repo.store._path_for(tip).exists()


def test_invalid_disk_usage_value_fails_cleanly(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--disk-usage=decimal", "HEAD")

    assert result.returncode != 0
    assert b"only accepts the optional value 'human'" in result.stderr


def test_installed_help_lists_phase141_disk_usage_option(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "rev-list", "--help")

    assert result.returncode == 0
    assert b"--disk-usage" in result.stdout
    assert b"--header" in result.stdout
