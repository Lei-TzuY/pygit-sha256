"""Phase 106 tests: multi-pack-index batch repacking."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.multi_pack_index import parse_multi_pack_index, verify_multi_pack_index, write_multi_pack_index
from pygit.multi_pack_index_expire import expire_multi_pack_index
from pygit.multi_pack_index_repack import repack_multi_pack_index
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


def _set_age(pack: Path, *, seconds: int) -> None:
    ns = seconds * 1_000_000_000
    os.utime(pack, ns=(ns, ns))


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_zero_batch_repacks_all_referenced_packs_then_expire_removes_old_batch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_a, idx_a, oids_a = _write_pack(repo, [b"alpha\n"], prefix="old-a")
    pack_b, idx_b, oids_b = _write_pack(repo, [b"beta\n"], prefix="old-b")
    pack_c, idx_c, oids_c = _write_pack(repo, [b"gamma\n"], prefix="old-c")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    old_indexes = {idx_a.name, idx_b.name, idx_c.name}
    all_oids = oids_a + oids_b + oids_c

    for oid in all_oids:
        assert repo.store.delete(oid)

    result = repack_multi_pack_index(repo, path, batch_size=0)

    assert result.created
    assert set(result.selected_packs) == old_indexes
    assert result.object_count == 3
    assert result.pack_path is not None and result.pack_path.is_file()
    assert result.idx_path is not None and result.idx_path.is_file()
    assert result.idx_path.name not in old_indexes

    verified = verify_multi_pack_index(path)
    for oid in all_oids:
        entry = verified.lookup(oid)
        assert entry is not None
        assert entry.pack_name == result.idx_path.name

    # Repack deliberately leaves the source batch in place. Phase 105 expire
    # then observes those packs as fully redundant and removes them.
    expired = expire_multi_pack_index(path)
    assert set(expired.expired_packs) == old_indexes
    assert expired.kept_packs == (result.idx_path.name,)
    for old_pack, old_idx in ((pack_a, idx_a), (pack_b, idx_b), (pack_c, idx_c)):
        assert not old_pack.exists()
        assert not old_idx.exists()

    for oid in all_oids:
        assert repo.store.read(oid).hash() == oid


def test_positive_batch_uses_oldest_to_newest_expected_size_threshold(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_a, idx_a, oids_a = _write_pack(repo, [b"same-size-a\n"], prefix="age-a")
    pack_b, idx_b, oids_b = _write_pack(repo, [b"same-size-b\n"], prefix="age-b")
    pack_c, idx_c, oids_c = _write_pack(repo, [b"same-size-c\n"], prefix="age-c")
    _set_age(pack_a, seconds=1_000)
    _set_age(pack_b, seconds=2_000)
    _set_age(pack_c, seconds=3_000)
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")

    # With one referenced object per pack, expected size equals pack size.
    # Pick a threshold just above the oldest pack so the first two packs form
    # the batch and the newest pack is never needed.
    threshold = pack_a.stat().st_size + 1
    assert pack_b.stat().st_size < threshold

    result = repack_multi_pack_index(repo, path, batch_size=threshold)

    assert result.created
    assert result.selected_packs == (idx_a.name, idx_b.name)
    assert result.expected_size >= threshold
    assert result.object_count == 2
    assert result.idx_path is not None

    verified = verify_multi_pack_index(path)
    for oid in oids_a + oids_b:
        assert verified.lookup(oid).pack_name == result.idx_path.name  # type: ignore[union-attr]
    assert verified.lookup(oids_c[0]).pack_name == idx_c.name  # type: ignore[union-attr]


def test_single_selected_pack_is_noop_and_does_not_rewrite_midx(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, idx, _ = _write_pack(repo, [b"only\n"], prefix="only")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    before = path.read_bytes()
    before_indexes = {item.name for item in path.parent.glob("*.idx")}

    result = repack_multi_pack_index(repo, path, batch_size=0)

    assert not result.created
    assert result.selected_packs == (idx.name,)
    assert result.object_count == 0
    assert path.read_bytes() == before
    assert {item.name for item in path.parent.glob("*.idx")} == before_indexes


def test_keep_pack_is_not_selected_but_other_packs_can_repack(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, kept_idx, kept_oids = _write_pack(repo, [b"kept\n"], prefix="kept")
    _, idx_b, oids_b = _write_pack(repo, [b"batch-b\n"], prefix="batch-b")
    _, idx_c, oids_c = _write_pack(repo, [b"batch-c\n"], prefix="batch-c")
    kept_idx.with_suffix(".keep").write_text("protected\n", encoding="utf-8")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")

    result = repack_multi_pack_index(repo, path, batch_size=0)

    assert result.created
    assert kept_idx.name not in result.selected_packs
    assert set(result.selected_packs) == {idx_b.name, idx_c.name}
    assert result.idx_path is not None
    verified = verify_multi_pack_index(path)
    assert verified.lookup(kept_oids[0]).pack_name == kept_idx.name  # type: ignore[union-attr]
    for oid in oids_b + oids_c:
        assert verified.lookup(oid).pack_name == result.idx_path.name  # type: ignore[union-attr]


def test_corrupt_midx_fails_before_creating_any_pack(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pack(repo, [b"one\n"], prefix="one")
    _write_pack(repo, [b"two\n"], prefix="two")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    before_indexes = {item.name for item in path.parent.glob("*.idx")}
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)

    with pytest.raises(ValueError, match="checksum mismatch"):
        repack_multi_pack_index(repo, path, batch_size=0)

    assert {item.name for item in path.parent.glob("*.idx")} == before_indexes


def test_corrupt_selected_pack_fails_without_rewriting_midx(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pack_a, _, _ = _write_pack(repo, [b"one\n"], prefix="one")
    _write_pack(repo, [b"two\n"], prefix="two")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    before = path.read_bytes()
    before_indexes = {item.name for item in path.parent.glob("*.idx")}

    damaged = bytearray(pack_a.read_bytes())
    damaged[-1] ^= 1
    pack_a.write_bytes(damaged)

    with pytest.raises(ValueError, match="checksum mismatch"):
        repack_multi_pack_index(repo, path, batch_size=0)

    assert path.read_bytes() == before
    assert {item.name for item in path.parent.glob("*.idx")} == before_indexes


def test_failure_after_pack_install_rolls_back_midx_and_generated_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    _write_pack(repo, [b"one\n"], prefix="one")
    _write_pack(repo, [b"two\n"], prefix="two")
    path = write_multi_pack_index(repo.pygit_dir / "objects" / "pack")
    before = path.read_bytes()
    before_indexes = {item.name for item in path.parent.glob("*.idx")}

    import pygit.multi_pack_index_repack as module

    def fail_rewrite(pack_dir: Path, preferred_idx: str) -> Path:
        raise RuntimeError("synthetic rewrite failure")

    monkeypatch.setattr(module, "_write_preferred_multi_pack_index", fail_rewrite)

    with pytest.raises(RuntimeError, match="synthetic rewrite failure"):
        module.repack_multi_pack_index(repo, path, batch_size=0)

    assert path.read_bytes() == before
    assert {item.name for item in path.parent.glob("*.idx")} == before_indexes


def test_cli_repack_batch_size_suffix_verify_and_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_pack(repo, [b"one\n"], prefix="one")
    _write_pack(repo, [b"two\n"], prefix="two")
    write_multi_pack_index(repo.pygit_dir / "objects" / "pack")

    result = _run(repo, "multi-pack-index", "repack", "--batch-size=1k")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""

    verified = _run(repo, "multi-pack-index", "verify")
    assert verified.returncode == 0, verified.stderr

    help_result = _run(repo, "multi-pack-index", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "repack" in help_result.stdout

    repack_help = _run(repo, "multi-pack-index", "repack", "--help")
    assert repack_help.returncode == 0, repack_help.stderr
    assert "--batch-size" in repack_help.stdout


def test_cli_rejects_negative_batch_size(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(repo, "multi-pack-index", "repack", "--batch-size=-1")
    assert result.returncode == 2
    assert "batch size must be non-negative" in result.stderr
