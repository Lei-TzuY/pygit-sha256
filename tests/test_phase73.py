"""Phase 73 tests: conservative repack maintenance."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

from pygit import Repository, repack
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.pack import PackWriter
from pygit.pack_index import parse_index
from pygit.pack_plumbing import parse_pack


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _loose(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _commit(repo: Repository, payload: bytes, message: str, parent: Optional[str] = None):
    blob_obj = BlobObject(payload)
    blob = repo.store.write(blob_obj)
    tree_obj = TreeObject([TreeEntry("100644", "file.txt", blob)])
    tree = repo.store.write(tree_obj)
    ident = Identity("Tester", "tester@example.com", 1, "+0000")
    commit_obj = CommitObject(
        tree=tree,
        parents=[parent] if parent else [],
        author=ident,
        committer=ident,
        message=message,
    )
    commit = repo.store.write(commit_obj)
    return (blob, tree, commit), (blob_obj, tree_obj, commit_obj)


def _set_main(repo: Repository, oid: str) -> None:
    repo.refs.set_branch("main", oid)
    repo.refs.set_head_symbolic("main")


def _pack(repo: Repository, pairs, prefix: str):
    return PackWriter(list(pairs)).write_pack_and_idx(repo.store.root / "pack", prefix)


def _pair_entries(pack_path: Path, idx_path: Path):
    pack = parse_pack(pack_path)
    index = parse_index(idx_path)
    return (
        {entry.oid: (entry.offset, entry.crc32) for entry in pack.entries},
        {entry.oid: (entry.offset, entry.crc32) for entry in index.entries},
    )


def test_default_repack_packs_only_unpacked_reachable_and_keeps_loose(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oids, _ = _commit(repo, b"reachable\n", "one")
    _set_main(repo, oids[-1])

    result = repack(repo)

    assert result.object_count == 3
    assert set(result.selected_oids) == set(oids)
    assert result.pack_path and result.pack_path.is_file()
    assert result.idx_path and result.idx_path.is_file()
    assert result.pruned_loose == 0
    assert all(_loose(repo, oid).is_file() for oid in oids)
    pack_meta, index_meta = _pair_entries(result.pack_path, result.idx_path)
    assert pack_meta == index_meta
    assert set(pack_meta) == set(oids)

    second = repack(repo)
    assert second.object_count == 0
    assert second.pack_path is None
    assert second.selected_oids == ()


def test_incremental_delete_keeps_old_pack_and_prunes_verified_loose_duplicates(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first_oids, first_objs = _commit(repo, b"first\n", "first")
    _set_main(repo, first_oids[-1])
    old_pack, old_idx = _pack(repo, zip(first_oids, first_objs), "old")

    second_oids, _ = _commit(repo, b"second\n", "second", parent=first_oids[-1])
    _set_main(repo, second_oids[-1])

    result = repack(repo, delete_redundant=True)

    assert set(result.selected_oids) == set(second_oids)
    assert result.removed_packs == ()
    assert old_pack.is_file() and old_idx.is_file()
    assert result.pruned_loose == 6
    assert all(not _loose(repo, oid).exists() for oid in (*first_oids, *second_oids))
    assert repo.store.read(second_oids[-1]).type_name == b"commit"


def test_all_delete_consolidates_fully_subsumed_old_pairs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first_oids, first_objs = _commit(repo, b"first\n", "first")
    second_oids, second_objs = _commit(repo, b"second\n", "second", parent=first_oids[-1])
    _set_main(repo, second_oids[-1])

    left_pack, left_idx = _pack(repo, zip(first_oids, first_objs), "left")
    right_pack, right_idx = _pack(repo, zip(second_oids, second_objs), "right")

    result = repack(repo, all_objects=True, delete_redundant=True)

    expected = set(first_oids) | set(second_oids)
    assert set(result.selected_oids) == expected
    assert set(result.removed_packs) == {left_pack.name, right_pack.name}
    assert not left_pack.exists() and not left_idx.exists()
    assert not right_pack.exists() and not right_idx.exists()
    assert result.pack_path and result.pack_path.is_file()
    assert result.idx_path and result.idx_path.is_file()
    assert result.pruned_loose == len(expected)
    assert all(not _loose(repo, oid).exists() for oid in expected)
    assert all(repo.store.read(oid) is not None for oid in expected)

    packs = list((repo.store.root / "pack").glob("*.pack"))
    indexes = list((repo.store.root / "pack").glob("*.idx"))
    assert packs == [result.pack_path]
    assert indexes == [result.idx_path]


def test_old_pack_with_unreachable_extra_is_never_deleted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    reachable_oids, reachable_objs = _commit(repo, b"reachable\n", "reachable")
    _set_main(repo, reachable_oids[-1])

    dangling_obj = BlobObject(b"recovery-only\n")
    dangling_oid = repo.store.write(dangling_obj)
    mixed_pack, mixed_idx = _pack(
        repo,
        [
            (reachable_oids[0], reachable_objs[0]),
            (dangling_oid, dangling_obj),
        ],
        "mixed",
    )

    result = repack(repo, all_objects=True, delete_redundant=True)

    assert mixed_pack.is_file() and mixed_idx.is_file()
    assert mixed_pack.name not in result.removed_packs
    assert dangling_oid not in result.selected_oids
    assert repo.store.read(dangling_oid).data == b"recovery-only\n"


def test_corrupt_pack_pair_aborts_before_any_new_pack_or_deletion(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oids, objs = _commit(repo, b"healthy loose\n", "healthy")
    _set_main(repo, oids[-1])
    bad_pack, bad_idx = _pack(repo, [(oids[0], objs[0])], "bad")
    damaged = bytearray(bad_idx.read_bytes())
    damaged[-1] ^= 1
    bad_idx.write_bytes(damaged)

    before = sorted(path.name for path in (repo.store.root / "pack").iterdir())
    with pytest.raises(RuntimeError, match="unhealthy repository"):
        repack(repo, all_objects=True, delete_redundant=True)
    after = sorted(path.name for path in (repo.store.root / "pack").iterdir())

    assert after == before
    assert bad_pack.is_file() and bad_idx.is_file()
    assert all(_loose(repo, oid).is_file() for oid in oids)


def test_dry_run_reports_plan_without_repository_storage_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oids, objs = _commit(repo, b"dry-run\n", "dry")
    _set_main(repo, oids[-1])
    old_pack, old_idx = _pack(repo, [(oids[0], objs[0])], "old")

    pack_dir = repo.store.root / "pack"
    before_names = sorted(path.name for path in pack_dir.iterdir())
    before_loose = {oid: _loose(repo, oid).read_bytes() for oid in oids}

    result = repack(repo, all_objects=True, delete_redundant=True, dry_run=True)

    assert result.dry_run
    assert result.object_count == 3
    assert old_pack.name in result.removed_packs
    assert set(result.loose_candidates) == set(oids)
    assert sorted(path.name for path in pack_dir.iterdir()) == before_names
    assert old_pack.is_file() and old_idx.is_file()
    assert {oid: _loose(repo, oid).read_bytes() for oid in oids} == before_loose


def test_installed_cli_routes_repack_and_reports_verbose_dry_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oids, _ = _commit(repo, b"cli\n", "cli")
    _set_main(repo, oids[-1])

    result = subprocess.run(
        [sys.executable, "-m", "pygit", "repack", "-a", "-d", "--dry-run", "--verbose"],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "would pack 3 object(s)" in result.stdout
    assert all(oid in result.stdout for oid in oids)
    assert not (repo.store.root / "pack").exists()
