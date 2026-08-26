"""Phase 110 tests: selective multi-pack-index ``write --stdin-packs``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import parse_multi_pack_index, write_multi_pack_index
from pygit.multi_pack_index_stdin import write_multi_pack_index_from_packs
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


def _run(
    repo: Repository,
    *args: str,
    input_text: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_stdin_writer_tracks_only_selected_packs_and_store_falls_back(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, idx_a, oids_a = _write_pack(repo, [b"selected-a\n"], prefix="pack-a")
    _, idx_b, oids_b = _write_pack(repo, [b"selected-b\n"], prefix="pack-b")
    _, idx_c, oids_c = _write_pack(repo, [b"unselected-c\n"], prefix="pack-c")
    pack_dir = repo.pygit_dir / "objects" / "pack"

    result = write_multi_pack_index_from_packs(
        pack_dir,
        [idx_b.name + "\n", idx_a.name + "\n"],
    )
    parsed = parse_multi_pack_index(result.path)

    assert result.pack_names == tuple(sorted((idx_a.name, idx_b.name)))
    assert parsed.pack_names == result.pack_names
    assert parsed.object_count == 2
    assert parsed.lookup(oids_a[0]) is not None
    assert parsed.lookup(oids_b[0]) is not None
    assert parsed.lookup(oids_c[0]) is None

    # Force reads through packed storage. The MIDX-covered packs use the fast
    # path while the excluded pack remains discoverable through ObjectStore's
    # stale/uncovered-pack fallback.
    for oid in (*oids_a, *oids_b, *oids_c):
        assert repo.store.delete(oid)
    assert repo.store.read(oids_a[0]).data == b"selected-a\n"
    assert repo.store.read(oids_b[0]).data == b"selected-b\n"
    assert repo.store.read(oids_c[0]).data == b"unselected-c\n"
    assert idx_c.is_file()


def test_stdin_records_ignore_blank_missing_malformed_and_duplicates(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, idx_a, _ = _write_pack(repo, [b"a\n"], prefix="a")
    _, idx_b, _ = _write_pack(repo, [b"b\n"], prefix="b")
    pack_dir = repo.pygit_dir / "objects" / "pack"

    result = write_multi_pack_index_from_packs(
        pack_dir,
        [
            "\n",
            "missing.idx\n",
            idx_a.name + "\n",
            idx_a.name + "\n",
            " " + idx_b.name + "\n",
            idx_b.name + "\r\n",
            "pack/not-a-basename.idx\n",
        ],
    )

    assert result.pack_names == tuple(sorted((idx_a.name, idx_b.name)))
    assert parse_multi_pack_index(result.path).pack_names == result.pack_names


def test_no_valid_stdin_pack_fails_without_replacing_existing_midx(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, idx, _ = _write_pack(repo, [b"stable\n"], prefix="stable")
    pack_dir = repo.pygit_dir / "objects" / "pack"
    path = write_multi_pack_index(pack_dir)
    before = path.read_bytes()

    with pytest.raises(ValueError, match="without selected pack indexes"):
        write_multi_pack_index_from_packs(
            pack_dir,
            ["\n", "missing.idx\n", idx.name + " \n"],
        )

    assert path.read_bytes() == before


def test_selected_missing_pack_fails_without_replacing_existing_midx(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, stable_idx, _ = _write_pack(repo, [b"stable\n"], prefix="stable")
    missing_pack, missing_idx, _ = _write_pack(
        repo, [b"will-lose-pack\n"], prefix="missing"
    )
    pack_dir = repo.pygit_dir / "objects" / "pack"
    path = write_multi_pack_index(pack_dir)
    before = path.read_bytes()
    missing_pack.unlink()

    with pytest.raises(FileNotFoundError):
        write_multi_pack_index_from_packs(pack_dir, [missing_idx.name + "\n"])

    assert path.read_bytes() == before
    assert stable_idx.is_file()


def test_stdin_writer_preserves_phase108_mtime_and_preferred_selection(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    shared = b"shared-stdin-preference\n"
    old_pack, old_idx, old_oids = _write_pack(
        repo, [shared, b"old-only\n"], prefix="z-old"
    )
    new_pack, new_idx, new_oids = _write_pack(
        repo, [shared, b"new-only\n"], prefix="a-new"
    )
    assert old_oids[0] == new_oids[0]
    _set_mtime(old_pack, old_idx, 1_700_000_000)
    _set_mtime(new_pack, new_idx, 1_700_000_100)
    pack_dir = repo.pygit_dir / "objects" / "pack"

    default = write_multi_pack_index_from_packs(
        pack_dir,
        [old_idx.name + "\n", new_idx.name + "\n"],
    )
    assert parse_multi_pack_index(default.path).lookup(old_oids[0]).pack_name == old_idx.name

    explicit = write_multi_pack_index_from_packs(
        pack_dir,
        [old_idx.name + "\n", new_idx.name + "\n"],
        preferred_pack=new_pack.name,
    )
    assert explicit.ignored_preferred_pack is None
    assert parse_multi_pack_index(explicit.path).lookup(old_oids[0]).pack_name == new_idx.name


def test_cli_unknown_preferred_pack_warns_and_uses_selected_default(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    shared = b"shared-cli-fallback\n"
    old_pack, old_idx, old_oids = _write_pack(repo, [shared], prefix="old")
    new_pack, new_idx, new_oids = _write_pack(repo, [shared], prefix="new")
    preferred_pack, preferred_idx, _ = _write_pack(
        repo, [b"outside-selection\n"], prefix="outside"
    )
    assert old_oids == new_oids
    _set_mtime(old_pack, old_idx, 1_700_000_000)
    _set_mtime(new_pack, new_idx, 1_700_000_100)

    result = _run(
        repo,
        "multi-pack-index",
        "write",
        "--stdin-packs",
        f"--preferred-pack={preferred_pack.name}",
        input_text=old_idx.name + "\n" + new_idx.name + "\n",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "warning: unknown preferred pack" in result.stderr
    parsed = parse_multi_pack_index(
        repo.pygit_dir / "objects" / "pack" / "multi-pack-index"
    )
    assert parsed.pack_names == tuple(sorted((old_idx.name, new_idx.name)))
    assert preferred_idx.name not in parsed.pack_names
    assert parsed.lookup(old_oids[0]).pack_name == old_idx.name


def test_installed_cli_stdin_packs_subset_and_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx_a, _ = _write_pack(repo, [b"cli-a\n"], prefix="cli-a")
    _, idx_b, _ = _write_pack(repo, [b"cli-b\n"], prefix="cli-b")
    _, idx_c, _ = _write_pack(repo, [b"cli-c\n"], prefix="cli-c")

    result = _run(
        repo,
        "multi-pack-index",
        "write",
        "--stdin-packs",
        input_text=idx_c.name + "\nmissing.idx\n\n" + idx_a.name + "\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""

    parsed = parse_multi_pack_index(
        repo.pygit_dir / "objects" / "pack" / "multi-pack-index"
    )
    assert parsed.pack_names == tuple(sorted((idx_a.name, idx_c.name)))
    assert idx_b.name not in parsed.pack_names

    verified = _run(repo, "multi-pack-index", "verify")
    assert verified.returncode == 0, verified.stderr

    help_result = _run(repo, "multi-pack-index", "write", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--stdin-packs" in help_result.stdout
