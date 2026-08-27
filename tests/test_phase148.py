"""Phase 148 tests: checkout-index temp files, --stage=all, and --stdin."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional, Union

import pytest

from pygit import Repository, checkout_index_temp
from pygit.index import IndexEntry
from pygit.objects import BlobObject


InputData = Optional[Union[str, bytes]]


def _write(repo: Repository, path: str, text: str) -> None:
    target = repo.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _conflicted_repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "conflict.txt", "base\n")
    _write(repo, "stable.txt", "stable\n")
    repo.add(["conflict.txt", "stable.txt"])
    repo.commit("base")

    repo.branch("feature")
    _write(repo, "conflict.txt", "theirs\n")
    repo.add(["conflict.txt"])
    repo.commit("theirs")

    repo.checkout("main")
    _write(repo, "conflict.txt", "ours\n")
    repo.add(["conflict.txt"])
    repo.commit("ours")

    result = repo.merge("feature")
    assert result["status"] == "conflicts"
    assert result["conflicts"] == ["conflict.txt"]
    return repo


def _run(
    repo: Repository,
    *args: str,
    text: bool = True,
    input_data: InputData = None,
):
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
    )


def test_temp_all_exports_base_ours_theirs_without_touching_conflict(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)
    before = (repo.worktree / "conflict.txt").read_bytes()

    records = checkout_index_temp(repo, ["conflict.txt"], stage="all")

    assert len(records) == 1
    record = records[0]
    assert record.path == "conflict.txt"
    expected = {1: b"base\n", 2: b"ours\n", 3: b"theirs\n"}
    for stage, data in expected.items():
        temp_path = record.file_for(stage)
        assert temp_path is not None
        assert temp_path.parent == repo.worktree
        assert temp_path.name.startswith(".merge_file_")
        assert "/" not in temp_path.name
        assert not any(ch.isspace() for ch in temp_path.name)
        assert temp_path.read_bytes() == data

    assert (repo.worktree / "conflict.txt").read_bytes() == before
    assert repo.index.has_unmerged("conflict.txt")


def test_installed_stage_all_implies_temp_and_prints_native_mapping(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)

    result = _run(repo, "checkout-index", "--stage=all", "conflict.txt")

    assert result.returncode == 0, result.stderr
    mapping, tracked = result.stdout.rstrip("\n").split("\t", 1)
    names = mapping.split(" ")
    assert tracked == "conflict.txt"
    assert len(names) == 3
    assert [(repo.worktree / name).read_text(encoding="utf-8") for name in names] == [
        "base\n",
        "ours\n",
        "theirs\n",
    ]


def test_stage_all_omits_stage_zero_only_paths_even_when_named(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)

    named = _run(repo, "checkout-index", "--stage=all", "stable.txt")
    assert named.returncode == 0, named.stderr
    assert named.stdout == ""

    all_result = _run(repo, "checkout-index", "--stage=all", "--all")
    assert all_result.returncode == 0, all_result.stderr
    assert all_result.stdout.count("\n") == 1
    assert all_result.stdout.endswith("\tconflict.txt\n")
    assert "stable.txt" not in all_result.stdout


def test_asymmetric_stage_all_uses_dot_for_missing_side(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "gone.txt", "base\n")
    repo.add(["gone.txt"])
    repo.commit("base")

    repo.branch("delete-side")
    repo.rm("gone.txt")
    repo.commit("delete theirs")

    repo.checkout("main")
    _write(repo, "gone.txt", "ours changed\n")
    repo.add(["gone.txt"])
    repo.commit("modify ours")
    assert repo.merge("delete-side")["status"] == "conflicts"

    result = _run(repo, "checkout-index", "--stage=all", "gone.txt")
    assert result.returncode == 0, result.stderr
    mapping, tracked = result.stdout.rstrip("\n").split("\t", 1)
    stage1, stage2, stage3 = mapping.split(" ")
    assert tracked == "gone.txt"
    assert stage3 == "."
    assert (repo.worktree / stage1).read_text(encoding="utf-8") == "base\n"
    assert (repo.worktree / stage2).read_text(encoding="utf-8") == "ours changed\n"


def test_numeric_temp_mapping_and_prefix_is_ignored(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)

    result = _run(
        repo,
        "checkout-index",
        "--temp",
        "--stage=2",
        "--prefix=ignored/",
        "conflict.txt",
    )

    assert result.returncode == 0, result.stderr
    temp_name, tracked = result.stdout.rstrip("\n").split("\t", 1)
    assert tracked == "conflict.txt"
    temp_path = repo.worktree / temp_name
    assert temp_path.parent == repo.worktree
    assert temp_path.read_text(encoding="utf-8") == "ours\n"
    assert not (repo.worktree / "ignored" / temp_name).exists()


def test_temp_mapping_supports_nul_record_separator(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)

    result = _run(repo, "checkout-index", "--stage=all", "-z", "conflict.txt", text=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(b"\0")
    assert b"\n" not in result.stdout
    assert result.stdout[:-1].endswith(b"\tconflict.txt")


def test_temp_symlink_entry_is_written_as_regular_file(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    oid = repo.store.write(BlobObject(b"target.txt"))
    repo.index.entries["link"] = IndexEntry("link", oid, "120000", 10, 0.0)
    repo.index.save()

    record = checkout_index_temp(repo, ["link"], stage=0)[0]
    temp_path = record.file_for(0)

    assert temp_path is not None
    assert temp_path.is_file()
    assert not temp_path.is_symlink()
    assert temp_path.read_bytes() == b"target.txt"


def test_temp_prevalidates_objects_before_creating_any_files(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    good_oid = repo.store.write(BlobObject(b"good\n"))
    repo.index.entries["a-good"] = IndexEntry("a-good", good_oid, "100644", 5, 0.0)
    repo.index.entries["z-missing"] = IndexEntry(
        "z-missing",
        "f" * 64,
        "100644",
        0,
        0.0,
    )
    repo.index.save()

    with pytest.raises((FileNotFoundError, KeyError, ValueError)):
        checkout_index_temp(repo, all_entries=True, stage=0)

    assert list(repo.worktree.glob(".merge_file_*")) == []


def test_stdin_lf_selects_paths_for_normal_checkout(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "a.txt", "alpha\n")
    _write(repo, "dir/b.txt", "beta\n")
    repo.add(["a.txt", "dir/b.txt"])
    (repo.worktree / "a.txt").unlink()
    (repo.worktree / "dir/b.txt").unlink()

    result = _run(
        repo,
        "checkout-index",
        "--stdin",
        input_data="a.txt\ndir/b.txt\n",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert (repo.worktree / "a.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (repo.worktree / "dir/b.txt").read_text(encoding="utf-8") == "beta\n"


def test_stdin_nul_selects_paths_for_normal_checkout(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    _write(repo, "a.txt", "alpha\n")
    _write(repo, "b.txt", "beta\n")
    repo.add(["a.txt", "b.txt"])
    (repo.worktree / "a.txt").unlink()
    (repo.worktree / "b.txt").unlink()

    result = _run(
        repo,
        "checkout-index",
        "--stdin",
        "-z",
        input_data=b"a.txt\0b.txt\0",
        text=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert (repo.worktree / "a.txt").read_bytes() == b"alpha\n"
    assert (repo.worktree / "b.txt").read_bytes() == b"beta\n"


def test_stdin_nul_and_stage_all_use_nul_for_input_and_mapping(tmp_path: Path) -> None:
    repo = _conflicted_repo(tmp_path)

    result = _run(
        repo,
        "checkout-index",
        "--stage=all",
        "--stdin",
        "-z",
        input_data=b"conflict.txt\0",
        text=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(b"\0")
    assert result.stdout.count(b"\0") == 1
    record = result.stdout[:-1]
    mapping, tracked = record.split(b"\t", 1)
    names = mapping.split(b" ")
    assert tracked == b"conflict.txt"
    assert len(names) == 3
    assert [(repo.worktree / name.decode()).read_bytes() for name in names] == [
        b"base\n",
        b"ours\n",
        b"theirs\n",
    ]


def test_stdin_rejects_ambiguous_selection_modes(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    explicit = _run(
        repo,
        "checkout-index",
        "--stdin",
        "a.txt",
        input_data="a.txt\n",
    )
    assert explicit.returncode == 2
    assert "cannot be combined with explicit paths" in explicit.stderr

    all_entries = _run(
        repo,
        "checkout-index",
        "--stdin",
        "--all",
        input_data="a.txt\n",
    )
    assert all_entries.returncode == 2
    assert "cannot be combined with --all" in all_entries.stderr


def test_empty_stdin_and_empty_cli_are_noops(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))

    stdin_result = _run(repo, "checkout-index", "--stdin", input_data="")
    assert stdin_result.returncode == 0, stdin_result.stderr
    assert stdin_result.stdout == ""

    empty_result = _run(repo, "checkout-index")
    assert empty_result.returncode == 0, empty_result.stderr
    assert empty_result.stdout == ""
