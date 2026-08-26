"""Phase 124 tests: persistent Git-style multi-stage index entries."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.index_plumbing import ls_files, refresh_index, update_index
from pygit.objects import BlobObject
from pygit.revision import resolve_revision


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _blob(repo: Repository, data: bytes) -> str:
    return repo.store.write(BlobObject(data))


def _three_stages(repo: Repository, path: str = "conflict.txt") -> tuple[str, str, str]:
    base = _blob(repo, b"base\n")
    ours = _blob(repo, b"ours\n")
    theirs = _blob(repo, b"theirs\n")
    update_index(
        repo,
        index_info=[
            f"100644 {base} 1\t{path}",
            f"100644 {ours} 2\t{path}",
            f"100644 {theirs} 3\t{path}",
        ],
    )
    return base, ours, theirs


def _run(
    repo: Repository,
    *args: str,
    input_text: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_index_info_persists_and_reloads_stages_1_2_3(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, ours, theirs = _three_stages(repo)

    assert repo.index.get("conflict.txt") is None
    assert repo.index.get("conflict.txt", 1).sha == base
    assert repo.index.get("conflict.txt", 2).sha == ours
    assert repo.index.get("conflict.txt", 3).sha == theirs
    assert repo.index.has_unmerged("conflict.txt")

    raw = json.loads((repo.pygit_dir / "index").read_text(encoding="utf-8"))
    assert [record["stage"] for record in raw] == [1, 2, 3]

    reopened = Repository(str(repo.worktree))
    assert reopened.index.get("conflict.txt", 1).sha == base
    assert reopened.index.get("conflict.txt", 2).sha == ours
    assert reopened.index.get("conflict.txt", 3).sha == theirs


def test_stage_zero_json_schema_stays_backward_compatible(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _blob(repo, b"normal\n")
    update_index(repo, cache_info=[("100644", oid, "normal.txt")])

    raw = json.loads((repo.pygit_dir / "index").read_text(encoding="utf-8"))
    assert len(raw) == 1
    assert raw[0]["path"] == "normal.txt"
    assert "stage" not in raw[0]


def test_shared_revision_resolver_reads_each_conflict_stage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, ours, theirs = _three_stages(repo)

    assert resolve_revision(repo, ":1:conflict.txt") == base
    assert resolve_revision(repo, ":2:conflict.txt") == ours
    assert resolve_revision(repo, ":3:conflict.txt") == theirs
    with pytest.raises(KeyError, match="not in the index"):
        resolve_revision(repo, ":conflict.txt")


def test_ls_files_stage_lists_every_index_stage_in_order(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base, ours, theirs = _three_stages(repo)

    assert ls_files(repo, stage=True) == [
        f"100644 {base} 1\tconflict.txt",
        f"100644 {ours} 2\tconflict.txt",
        f"100644 {theirs} 3\tconflict.txt",
    ]
    # `--cached` reports one pathname per index record, just like Git does for
    # an unmerged path when stage metadata is not requested.
    assert ls_files(repo, cached=True) == [
        "conflict.txt",
        "conflict.txt",
        "conflict.txt",
    ]


def test_mode_zero_index_info_removes_every_stage_for_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)

    update_index(repo, index_info=[f"0 {'0' * 64}\tconflict.txt"])

    assert repo.index.get("conflict.txt") is None
    assert repo.index.stage_entries("conflict.txt") == []
    assert "conflict.txt" not in repo.index
    assert ls_files(repo, stage=True) == []


def test_normal_worktree_update_resolves_unmerged_path_to_stage_zero(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)
    target = repo.worktree / "conflict.txt"
    target.write_bytes(b"resolved\n")

    # An unmerged path counts as tracked, so no --add is needed to resolve it.
    update_index(repo, ["conflict.txt"])

    entry = repo.index.get("conflict.txt")
    assert entry is not None
    assert repo.store.read(entry.sha).serialize() == b"resolved\n"
    assert repo.index.stage_entries("conflict.txt") == []
    assert ls_files(repo, stage=True) == [
        f"100644 {entry.sha} 0\tconflict.txt"
    ]


def test_cacheinfo_stage_zero_can_coexist_with_explicit_conflict_stages(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base, ours, theirs = _three_stages(repo)
    resolved = _blob(repo, b"candidate\n")

    update_index(repo, cache_info=[("100644", resolved, "conflict.txt")])

    assert repo.index.get("conflict.txt").sha == resolved
    assert repo.index.get("conflict.txt", 1).sha == base
    assert repo.index.get("conflict.txt", 2).sha == ours
    assert repo.index.get("conflict.txt", 3).sha == theirs


def test_refresh_marks_unmerged_path_dirty_instead_of_crashing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _three_stages(repo)

    assert refresh_index(repo) == ["conflict.txt"]
    assert refresh_index(repo, ["conflict.txt"]) == ["conflict.txt"]


def test_invalid_index_info_stage_is_atomic(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _blob(repo, b"first\n")
    second = _blob(repo, b"second\n")

    with pytest.raises(ValueError, match="stage"):
        update_index(
            repo,
            index_info=[
                f"100644 {first} 1\tconflict.txt",
                f"100644 {second} 4\tconflict.txt",
            ],
        )

    assert repo.index.all_entries(include_unmerged=True) == []
    assert not (repo.pygit_dir / "index").exists()


def test_installed_cli_round_trip_for_index_info_ls_files_and_revision(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    base = _blob(repo, b"base\n")
    ours = _blob(repo, b"ours\n")
    theirs = _blob(repo, b"theirs\n")
    records = (
        f"100644 {base} 1\tconflict.txt\n"
        f"100644 {ours} 2\tconflict.txt\n"
        f"100644 {theirs} 3\tconflict.txt\n"
    )

    update = _run(repo, "update-index", "--index-info", input_text=records)
    assert update.returncode == 0, update.stderr
    assert update.stdout == ""

    staged = _run(repo, "ls-files", "--stage")
    assert staged.returncode == 0, staged.stderr
    assert staged.stdout == (
        f"100644 {base} 1\tconflict.txt\n"
        f"100644 {ours} 2\tconflict.txt\n"
        f"100644 {theirs} 3\tconflict.txt\n"
    )

    parsed = _run(repo, "rev-parse", ":2:conflict.txt")
    assert parsed.returncode == 0, parsed.stderr
    assert parsed.stdout == ours + "\n"

    cat = _run(repo, "cat-file", "-p", ":3:conflict.txt")
    assert cat.returncode == 0, cat.stderr
    assert cat.stdout == "theirs\n"


def test_phase118_colon_disambiguation_remains_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = _blob(repo, b"colon\n")
    update_index(repo, cache_info=[("100644", oid, "4:name")])

    assert resolve_revision(repo, ":4:name") == oid
    with pytest.raises(KeyError, match="stage 1"):
        resolve_revision(repo, ":1:4:name")
