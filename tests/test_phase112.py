"""Phase 112 tests: multi-pack-index alternate object-directory routing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.multi_pack_index import parse_multi_pack_index
from pygit.objects import BlobObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _alternate(repo: Repository, tmp_path: Path, *, relative: bool = False) -> Path:
    object_dir = repo.pygit_dir / "objects"
    alternate = tmp_path / "alternate-objects"
    (alternate / "pack").mkdir(parents=True)
    info = object_dir / "info"
    info.mkdir(parents=True, exist_ok=True)
    value = os.path.relpath(alternate, object_dir) if relative else str(alternate)
    (info / "alternates").write_text(value + "\n", encoding="utf-8")
    return alternate.resolve()


def _write_pack(object_dir: Path, payloads: list[bytes], prefix: str):
    pairs = []
    oids = []
    for payload in payloads:
        obj = BlobObject(payload)
        oid = obj.hash()
        pairs.append((oid, obj))
        oids.append(oid)
    pack, idx = PackWriter(pairs).write_pack_and_idx(object_dir / "pack", prefix)
    return pack, idx, tuple(oids)


def _run(repo: Repository, *args: str, input_text: str = ""):
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_object_dir_write_and_verify_use_absolute_alternate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = _alternate(repo, tmp_path)
    _, idx, oids = _write_pack(alternate, [b"alternate-write\n"], "alt")

    result = _run(repo, "multi-pack-index", "--object-dir", str(alternate), "write")
    assert result.returncode == 0, result.stderr
    midx = alternate / "pack" / "multi-pack-index"
    parsed = parse_multi_pack_index(midx)
    assert parsed.pack_names == (idx.name,)
    assert parsed.lookup(oids[0]) is not None
    assert not (repo.pygit_dir / "objects" / "pack" / "multi-pack-index").exists()

    verify = _run(repo, "multi-pack-index", "--object-dir", str(alternate), "verify")
    assert verify.returncode == 0, verify.stderr


def test_object_dir_accepts_relative_alternate_entry(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = _alternate(repo, tmp_path, relative=True)
    _write_pack(alternate, [b"relative-alternate\n"], "rel")

    result = _run(repo, "multi-pack-index", "--object-dir", str(alternate), "write")
    assert result.returncode == 0, result.stderr
    assert (alternate / "pack" / "multi-pack-index").is_file()


def test_object_dir_rejects_unconfigured_directory_before_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = _alternate(repo, tmp_path)
    _write_pack(alternate, [b"configured\n"], "configured")
    other = tmp_path / "other-objects"
    (other / "pack").mkdir(parents=True)
    _write_pack(other, [b"other\n"], "other")

    result = _run(repo, "multi-pack-index", "--object-dir", str(other), "write")
    assert result.returncode != 0
    assert "not a configured alternate" in result.stderr
    assert not (other / "pack" / "multi-pack-index").exists()


def test_object_dir_composes_with_stdin_packs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = _alternate(repo, tmp_path)
    _, idx_a, oids_a = _write_pack(alternate, [b"a\n"], "a")
    _, idx_b, oids_b = _write_pack(alternate, [b"b\n"], "b")

    result = _run(
        repo,
        "multi-pack-index",
        "--object-dir",
        str(alternate),
        "write",
        "--stdin-packs",
        input_text=idx_b.name + "\n",
    )
    assert result.returncode == 0, result.stderr
    parsed = parse_multi_pack_index(alternate / "pack" / "multi-pack-index")
    assert parsed.pack_names == (idx_b.name,)
    assert parsed.lookup(oids_a[0]) is None
    assert parsed.lookup(oids_b[0]) is not None
    assert idx_a.is_file()


def test_object_dir_expire_deletes_only_redundant_alternate_pack(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = _alternate(repo, tmp_path)
    shared = b"duplicate\n"
    pack_a, idx_a, _ = _write_pack(alternate, [shared], "a")
    pack_b, idx_b, _ = _write_pack(alternate, [shared], "b")
    os.utime(pack_a, (1_700_000_000, 1_700_000_000))
    os.utime(pack_b, (1_700_000_100, 1_700_000_100))

    write = _run(repo, "multi-pack-index", "--object-dir", str(alternate), "write")
    assert write.returncode == 0, write.stderr
    before = parse_multi_pack_index(alternate / "pack" / "multi-pack-index")
    assert before.pack_names == tuple(sorted((idx_a.name, idx_b.name)))

    expire = _run(repo, "multi-pack-index", "--object-dir", str(alternate), "expire")
    assert expire.returncode == 0, expire.stderr
    assert pack_a.is_file() and idx_a.is_file()
    assert not pack_b.exists() and not idx_b.exists()
    after = parse_multi_pack_index(alternate / "pack" / "multi-pack-index")
    assert after.pack_names == (idx_a.name,)


def test_object_dir_repack_installs_new_pack_only_in_alternate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alternate = _alternate(repo, tmp_path)
    _write_pack(alternate, [b"first\n"], "first")
    _write_pack(alternate, [b"second\n"], "second")

    write = _run(repo, "multi-pack-index", "--object-dir", str(alternate), "write")
    assert write.returncode == 0, write.stderr
    before_alt = set((alternate / "pack").glob("*.pack"))
    primary_pack = repo.pygit_dir / "objects" / "pack"
    primary_pack.mkdir(parents=True, exist_ok=True)
    before_primary = set(primary_pack.glob("*.pack"))

    repack = _run(
        repo,
        "multi-pack-index",
        "--object-dir",
        str(alternate),
        "repack",
        "--batch-size=0",
    )
    assert repack.returncode == 0, repack.stderr
    after_alt = set((alternate / "pack").glob("*.pack"))
    assert len(after_alt) == len(before_alt) + 1
    assert set(primary_pack.glob("*.pack")) == before_primary


def test_object_dir_is_documented_in_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(repo, "multi-pack-index", "--help")
    assert result.returncode == 0, result.stderr
    assert "--object-dir" in result.stdout
