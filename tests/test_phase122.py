"""Phase 122 tests: Git-style alternate pygit object databases."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import write_multi_pack_index
from pygit.objects import BlobObject
from pygit.pack import PackWriter
from pygit.revision import resolve_revision


def _repo(path: Path) -> Repository:
    return Repository.init(str(path))


def _loose_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _set_alternates(repo: Repository, *roots: Path) -> Path:
    info = repo.store.root / "info"
    info.mkdir(parents=True, exist_ok=True)
    path = info / "alternates"
    lines = [os.path.relpath(Path(root).resolve(), repo.store.root.resolve()) for root in roots]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_cat_file(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "cat-file", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_relative_alternate_exposes_loose_objects_short_ids_and_raw_bytes(tmp_path: Path) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    obj = BlobObject(b"borrowed loose object\n")
    oid = source.store.write(obj)
    _set_alternates(borrower, source.store.root)

    assert borrower.store.storage_roots() == (borrower.store.root.resolve(), source.store.root.resolve())
    assert borrower.store.exists(oid)
    assert borrower.store.read(oid).data == b"borrowed loose object\n"
    assert borrower.store.read_store_bytes(oid) == obj._build_store_bytes()
    assert oid in borrower.store.all_shas()
    assert borrower.store.resolve_prefix(oid[:16]) == oid
    assert resolve_revision(borrower, oid[:16]) == oid


def test_packed_only_alternate_uses_its_multi_pack_index_without_materializing(tmp_path: Path) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    obj = BlobObject(b"packed alternate\n")
    oid = source.store.write(obj)
    PackWriter([(oid, obj)]).write_pack_and_idx(source.store.root / "pack", "alt")
    _loose_path(source, oid).unlink()
    write_multi_pack_index(source.store.root / "pack")
    _set_alternates(borrower, source.store.root)

    assert borrower.store.exists(oid)
    assert borrower.store.read_store_bytes(oid) == obj._build_store_bytes()
    assert borrower.store.read(oid).data == b"packed alternate\n"
    assert borrower.store.resolve_prefix(oid[:12]) == oid
    assert not _loose_path(borrower, oid).exists()


def test_transitive_alternates_are_cycle_safe_and_deduplicated(tmp_path: Path) -> None:
    first = _repo(tmp_path / "first")
    second = _repo(tmp_path / "second")
    third = _repo(tmp_path / "third")
    oid = third.store.write(BlobObject(b"transitive\n"))
    _set_alternates(first, second.store.root)
    _set_alternates(second, third.store.root)
    _set_alternates(third, first.store.root)

    assert first.store.storage_roots() == (
        first.store.root.resolve(), second.store.root.resolve(), third.store.root.resolve()
    )
    assert first.store.read(oid).data == b"transitive\n"
    assert first.store.all_shas().count(oid) == 1


def test_missing_and_malformed_alternates_fail_loudly(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    info = repo.store.root / "info"
    info.mkdir(parents=True, exist_ok=True)
    path = info / "alternates"

    path.write_text("../does-not-exist\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="alternate object directory"):
        repo.store.storage_roots()

    path.write_bytes(b"../whatever\x00else\n")
    with pytest.raises(ValueError, match="contains NUL"):
        repo.store.storage_roots()


def test_writes_and_deletes_never_mutate_the_alternate_store(tmp_path: Path) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    obj = BlobObject(b"ownership boundary\n")
    oid = source.store.write(obj)
    source_path = _loose_path(source, oid)
    _set_alternates(borrower, source.store.root)

    assert borrower.store.write(obj) == oid
    assert _loose_path(borrower, oid).is_file()
    assert source_path.is_file()
    assert borrower.store.delete(oid)
    assert not _loose_path(borrower, oid).exists()
    assert source_path.is_file()
    assert borrower.store.read(oid).data == b"ownership boundary\n"


def test_alternate_corruption_can_fall_through_to_later_valid_copy(tmp_path: Path) -> None:
    bad = _repo(tmp_path / "bad")
    good = _repo(tmp_path / "good")
    borrower = _repo(tmp_path / "borrower")
    obj = BlobObject(b"redundant alternate\n")
    oid = bad.store.write(obj)
    assert good.store.write(obj) == oid
    _loose_path(bad, oid).write_bytes(b"not-zlib")
    _set_alternates(borrower, bad.store.root, good.store.root)

    assert borrower.store.read_store_bytes(oid) == obj._build_store_bytes()
    assert borrower.store.read(oid).data == b"redundant alternate\n"


def test_primary_corruption_is_not_hidden_by_a_valid_alternate(tmp_path: Path) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    obj = BlobObject(b"primary authority\n")
    oid = source.store.write(obj)
    assert borrower.store.write(obj) == oid
    _set_alternates(borrower, source.store.root)
    _loose_path(borrower, oid).write_bytes(b"not-zlib")

    with pytest.raises(Exception) as excinfo:
        borrower.store.read_store_bytes(oid)
    assert "Object not found" not in str(excinfo.value)


def test_batch_all_objects_includes_alternates_once_in_both_orders(tmp_path: Path) -> None:
    source = _repo(tmp_path / "source")
    borrower = _repo(tmp_path / "borrower")
    shared = BlobObject(b"shared\n")
    borrowed = BlobObject(b"borrowed\n")
    local = BlobObject(b"local\n")

    shared_oid = source.store.write(shared)
    borrowed_oid = source.store.write(borrowed)
    assert borrower.store.write(shared) == shared_oid
    local_oid = borrower.store.write(local)
    _set_alternates(borrower, source.store.root)
    expected = {shared_oid, borrowed_oid, local_oid}

    ordered = _run_cat_file(borrower, "--batch-check", "--batch-all-objects")
    assert ordered.returncode == 0, ordered.stderr.decode("utf-8", "replace")
    ordered_oids = [line.split(b" ", 1)[0].decode("ascii") for line in ordered.stdout.splitlines()]
    assert ordered_oids == sorted(expected)

    unordered = _run_cat_file(borrower, "--batch-check", "--batch-all-objects", "--unordered")
    assert unordered.returncode == 0, unordered.stderr.decode("utf-8", "replace")
    unordered_oids = [line.split(b" ", 1)[0].decode("ascii") for line in unordered.stdout.splitlines()]
    assert len(unordered_oids) == len(set(unordered_oids)) == 3
    assert set(unordered_oids) == expected
