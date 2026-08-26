"""Phase 125 tests: Phase 123 alternates × Phase 124 multi-stage index."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.index_plumbing import ls_files, update_index
from pygit.multi_pack_index import write_multi_pack_index
from pygit.objects import BlobObject
from pygit.pack import PackWriter
from pygit.revision import resolve_revision


def _repo(path: Path) -> Repository:
    return Repository.init(str(path))


def _loose_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _set_alternate(borrower: Repository, source: Repository) -> Path:
    info = borrower.store.root / "info"
    info.mkdir(parents=True, exist_ok=True)
    path = info / "alternates"
    relative = os.path.relpath(source.store.root.resolve(), borrower.store.root.resolve())
    path.write_text(relative + "\n", encoding="utf-8")
    return path


def _conflict_objects(source: Repository) -> tuple[tuple[str, BlobObject], ...]:
    objects = (
        BlobObject(b"base from alternate\n"),
        BlobObject(b"ours from alternate\n"),
        BlobObject(b"theirs from alternate\n"),
    )
    return tuple((source.store.write(obj), obj) for obj in objects)


def _stage_conflict(
    borrower: Repository,
    records: tuple[tuple[str, BlobObject], ...],
    path: str = "conflict.txt",
) -> None:
    update_index(
        borrower,
        index_info=[
            f"100644 {oid} {stage}\t{path}"
            for stage, (oid, _obj) in enumerate(records, start=1)
        ],
    )


def _run_cat_file(
    repo: Repository,
    expression: str,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "cat-file", "-p", expression],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_conflict_stages_can_reference_loose_alternate_objects(tmp_path: Path) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    records = _conflict_objects(source)
    _set_alternate(borrower, source)
    _stage_conflict(borrower, records)

    expected_oids = [oid for oid, _obj in records]
    assert [resolve_revision(borrower, f":{stage}:conflict.txt") for stage in (1, 2, 3)] == expected_oids
    assert ls_files(borrower, stage=True) == [
        f"100644 {oid} {stage}\tconflict.txt"
        for stage, oid in enumerate(expected_oids, start=1)
    ]

    # Merely indexing or resolving a borrowed conflict object must not create a
    # primary loose copy in the borrowing repository.
    assert all(not _loose_path(borrower, oid).exists() for oid in expected_oids)

    cat = _run_cat_file(borrower, ":2:conflict.txt")
    assert cat.returncode == 0, cat.stderr.decode("utf-8", "replace")
    assert cat.stderr == b""
    assert cat.stdout == b"ours from alternate\n"

    reopened = Repository(str(borrower.worktree))
    assert resolve_revision(reopened, ":3:conflict.txt") == expected_oids[2]


def test_packed_only_alternate_stages_resolve_through_midx_without_materializing(
    tmp_path: Path,
) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    records = _conflict_objects(source)

    PackWriter(list(records)).write_pack_and_idx(source.store.root / "pack", "conflicts")
    for oid, _obj in records:
        _loose_path(source, oid).unlink()
    write_multi_pack_index(source.store.root / "pack")

    _set_alternate(borrower, source)
    _stage_conflict(borrower, records, "packed.txt")

    for stage, (oid, obj) in enumerate(records, start=1):
        assert resolve_revision(borrower, f":{stage}:packed.txt") == oid
        assert borrower.store.read(oid).data == obj.data
        assert not _loose_path(borrower, oid).exists()

    cat = _run_cat_file(borrower, ":1:packed.txt")
    assert cat.returncode == 0, cat.stderr.decode("utf-8", "replace")
    assert cat.stdout == b"base from alternate\n"


def test_worktree_resolution_replaces_borrowed_stages_with_primary_stage_zero(
    tmp_path: Path,
) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    records = _conflict_objects(source)
    alternates = _set_alternate(borrower, source)
    _stage_conflict(borrower, records)

    target = borrower.worktree / "conflict.txt"
    target.write_bytes(b"resolved locally\n")
    update_index(borrower, ["conflict.txt"])

    stage_zero = borrower.index.get("conflict.txt")
    assert stage_zero is not None
    assert borrower.index.stage_entries("conflict.txt") == []
    assert borrower.store.read(stage_zero.sha).data == b"resolved locally\n"
    assert _loose_path(borrower, stage_zero.sha).is_file()

    # The source alternate is read-only from the borrower. The newly resolved
    # content is owned only by the primary object database.
    assert not source.store.exists(stage_zero.sha)
    assert all(source.store.exists(oid) for oid, _obj in records)

    # Once the conflict is resolved to stage 0, the borrower no longer depends
    # on the alternate for that path.
    alternates.unlink()
    reopened = Repository(str(borrower.worktree))
    assert resolve_revision(reopened, ":conflict.txt") == stage_zero.sha
    assert reopened.store.read(stage_zero.sha).data == b"resolved locally\n"
