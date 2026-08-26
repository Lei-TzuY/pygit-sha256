"""Phase 107 tests: full pack validation behind multi-pack-index verify."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import (
    parse_multi_pack_index,
    verify_multi_pack_index,
    write_multi_pack_index,
)
from pygit.multi_pack_index_expire import expire_multi_pack_index
from pygit.objects import BlobObject
from pygit.pack import PackWriter
from pygit.pack_index import parse_index


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


def _duplicate_pair(
    repo: Repository,
) -> tuple[Path, str, Path, Path]:
    shared = BlobObject(b"midx-redundant-safety\n")
    pack_a, idx_a, oids_a = _write_pack(
        repo, [shared.data], prefix="copy-a"
    )
    pack_b, idx_b, oids_b = _write_pack(
        repo, [shared.data], prefix="copy-b"
    )
    assert oids_a == oids_b
    oid = oids_a[0]
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    parsed = parse_multi_pack_index(path)
    selected_name = parsed.lookup(oid).pack_name  # type: ignore[union-attr]
    selected_idx = idx_a if idx_a.name == selected_name else idx_b
    alternate_idx = idx_b if selected_idx == idx_a else idx_a
    assert selected_idx != alternate_idx
    assert selected_idx.with_suffix(".pack") in {pack_a, pack_b}
    return path, oid, selected_idx, alternate_idx


def _damage_pack_trailer(idx_path: Path) -> None:
    pack_path = idx_path.with_suffix(".pack")
    data = bytearray(pack_path.read_bytes())
    data[-1] ^= 0x01
    pack_path.write_bytes(data)


def _damage_first_crc_with_valid_index_checksum(idx_path: Path) -> None:
    index = parse_index(idx_path)
    assert index.entries
    data = bytearray(idx_path.read_bytes())
    # pygit's idx-v2 layout is 8-byte header, 1024-byte fanout, then one
    # 64-byte ASCII SHA-256 object name per entry, followed by CRC32 values.
    crc_start = 8 + (256 * 4) + (len(index.entries) * 64)
    data[crc_start] ^= 0x01
    data[-32:] = hashlib.sha256(data[:-32]).digest()
    idx_path.write_bytes(data)
    # The strict index parser still accepts the image; only pack/index content
    # verification can detect that the CRC no longer describes the pack entry.
    parse_index(idx_path)


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_verify_rejects_corrupt_selected_pack_even_with_healthy_redundant_copy(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    path, oid, selected_idx, alternate_idx = _duplicate_pair(repo)
    assert repo.store.delete(oid)
    _damage_pack_trailer(selected_idx)

    with pytest.raises(ValueError, match="pack SHA-256 checksum mismatch"):
        verify_multi_pack_index(path)

    # Ordinary reads retain the Phase 104 redundant-copy fallback, proving that
    # the repository still has one healthy copy even though MIDX verification
    # correctly rejects the damaged selected source.
    assert alternate_idx.is_file()
    assert repo.store.read(oid).hash() == oid


def test_verify_checks_entry_crc_not_only_valid_index_checksum(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx, _ = _write_pack(repo, [b"crc-integrity\n"], prefix="crc")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    _damage_first_crc_with_valid_index_checksum(idx)

    with pytest.raises(ValueError, match="CRC"):
        verify_multi_pack_index(path)


def test_expire_fails_before_deleting_healthy_redundant_copy(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    path, oid, selected_idx, alternate_idx = _duplicate_pair(repo)
    assert repo.store.delete(oid)
    selected_pack = selected_idx.with_suffix(".pack")
    alternate_pack = alternate_idx.with_suffix(".pack")
    _damage_pack_trailer(selected_idx)

    before = path.read_bytes()
    with pytest.raises(ValueError, match="pack SHA-256 checksum mismatch"):
        expire_multi_pack_index(path)

    # The destructive lifecycle must abort before deleting the unreferenced but
    # healthy alternate pack. This is the data-loss scenario Phase 107 closes.
    assert path.read_bytes() == before
    assert selected_idx.is_file() and selected_pack.is_file()
    assert alternate_idx.is_file() and alternate_pack.is_file()
    assert repo.store.read(oid).hash() == oid


def test_expire_also_refuses_corrupt_redundant_pack_before_mutation(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    path, _oid, selected_idx, alternate_idx = _duplicate_pair(repo)
    _damage_pack_trailer(alternate_idx)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="pack SHA-256 checksum mismatch"):
        expire_multi_pack_index(path)

    assert path.read_bytes() == before
    for idx in (selected_idx, alternate_idx):
        assert idx.is_file()
        assert idx.with_suffix(".pack").is_file()


def test_cli_verify_surfaces_pack_corruption(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack, _idx, _ = _write_pack(repo, [b"cli-pack-integrity\n"], prefix="cli")
    write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    data = bytearray(pack.read_bytes())
    data[-1] ^= 0x01
    pack.write_bytes(data)

    result = _run(repo, "multi-pack-index", "verify")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "pack SHA-256 checksum mismatch" in result.stderr
