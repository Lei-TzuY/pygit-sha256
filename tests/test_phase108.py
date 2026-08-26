"""Phase 108 tests: Git-style multi-pack-index preferred-pack selection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import parse_multi_pack_index, write_multi_pack_index
from pygit.objects import BlobObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _write_pack(
    repo: Repository,
    payloads: list[bytes],
    *,
    prefix: str,
) -> tuple[Path, Path, tuple[str, ...]]:
    pairs = []
    oids = []
    for payload in payloads:
        obj = BlobObject(payload)
        oid = repo.store.write(obj)
        pairs.append((oid, obj))
        oids.append(oid)
    pack, idx = PackWriter(pairs).write_pack_and_idx(
        repo.pygit_dir / "objects" / "pack",
        name_prefix=prefix,
    )
    return pack, idx, tuple(oids)


def _set_mtime(pack: Path, idx: Path, timestamp: int) -> None:
    os.utime(pack, (timestamp, timestamp))
    os.utime(idx, (timestamp, timestamp))


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_default_write_prefers_oldest_pack_for_duplicate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shared = b"shared-default-preferred\n"
    old_pack, old_idx, old_oids = _write_pack(
        repo, [shared, b"old-only\n"], prefix="z-old"
    )
    new_pack, new_idx, new_oids = _write_pack(
        repo, [shared, b"new-only\n"], prefix="a-new"
    )
    assert old_oids[0] == new_oids[0]
    assert old_idx.name > new_idx.name
    _set_mtime(old_pack, old_idx, 1_700_000_000)
    _set_mtime(new_pack, new_idx, 1_700_000_100)

    parsed = parse_multi_pack_index(
        write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    )

    assert parsed.lookup(old_oids[0]).pack_name == old_idx.name


def test_explicit_preferred_pack_overrides_default_oldest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shared = b"shared-explicit-preferred\n"
    old_pack, old_idx, old_oids = _write_pack(repo, [shared], prefix="old")
    new_pack, new_idx, new_oids = _write_pack(repo, [shared], prefix="new")
    assert old_oids == new_oids
    _set_mtime(old_pack, old_idx, 1_700_000_000)
    _set_mtime(new_pack, new_idx, 1_700_000_100)

    path = write_multi_pack_index(
        repo.pygit_dir / "objects" / "pack",
        preferred_pack=new_pack.name,
    )
    parsed = parse_multi_pack_index(path)

    assert parsed.lookup(old_oids[0]).pack_name == new_idx.name


def test_nonpreferred_duplicate_uses_newest_pack_mtime(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    preferred_pack, preferred_idx, preferred_oids = _write_pack(
        repo, [b"preferred-only\n"], prefix="preferred"
    )
    older_pack, older_idx, older_oids = _write_pack(
        repo, [b"shared-nonpreferred\n"], prefix="older"
    )
    newer_pack, newer_idx, newer_oids = _write_pack(
        repo, [b"shared-nonpreferred\n"], prefix="newer"
    )
    assert older_oids == newer_oids
    _set_mtime(preferred_pack, preferred_idx, 1_700_000_000)
    _set_mtime(older_pack, older_idx, 1_700_000_050)
    _set_mtime(newer_pack, newer_idx, 1_700_000_100)

    parsed = parse_multi_pack_index(
        write_multi_pack_index(
            repo.pygit_dir / "objects" / "pack",
            preferred_pack=preferred_idx.name,
        )
    )

    assert parsed.lookup(preferred_oids[0]).pack_name == preferred_idx.name
    assert parsed.lookup(older_oids[0]).pack_name == newer_idx.name


def test_missing_preferred_pack_fails_without_replacing_existing_midx(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _write_pack(repo, [b"stable\n"], prefix="stable")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="preferred multi-pack-index pack is missing"):
        write_multi_pack_index(
            repo.pygit_dir / "objects" / "pack",
            preferred_pack="pack-does-not-exist.pack",
        )

    assert path.read_bytes() == before


def test_empty_explicit_preferred_pack_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_dir = repo.pygit_dir / "objects" / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    empty_pack, empty_idx = PackWriter([]).write_pack_and_idx(
        pack_dir, name_prefix="empty"
    )
    _write_pack(repo, [b"non-empty\n"], prefix="non-empty")

    with pytest.raises(ValueError, match="must contain at least one object"):
        write_multi_pack_index(pack_dir, preferred_pack=empty_pack.name)

    assert empty_idx.is_file()


def test_cli_write_preferred_pack_and_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shared = b"cli-preferred\n"
    old_pack, old_idx, old_oids = _write_pack(repo, [shared], prefix="cli-old")
    new_pack, new_idx, new_oids = _write_pack(repo, [shared], prefix="cli-new")
    assert old_oids == new_oids
    _set_mtime(old_pack, old_idx, 1_700_000_000)
    _set_mtime(new_pack, new_idx, 1_700_000_100)

    result = _run(
        repo,
        "multi-pack-index",
        "write",
        f"--preferred-pack={new_pack.name}",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""

    parsed = parse_multi_pack_index(
        repo.pygit_dir / "objects" / "pack" / "multi-pack-index"
    )
    assert parsed.lookup(old_oids[0]).pack_name == new_idx.name

    help_result = _run(repo, "multi-pack-index", "write", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--preferred-pack" in help_result.stdout
