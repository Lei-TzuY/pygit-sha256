"""Phase 95 tests: storage-local ``cat-file --unordered`` enumeration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, all_object_ids, format_batch_object
from pygit.object_enumeration import iter_object_ids
from pygit.objects import BlobObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _run(
    repo: Repository,
    *args: str,
    input_data: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "cat-file", *args],
        cwd=repo.worktree,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _loose_path(repo: Repository, oid: str) -> Path:
    return repo.store.root / oid[:2] / oid[2:]


def _pack(repo: Repository, *objects: BlobObject, prefix: str = "phase95") -> None:
    PackWriter([(obj.hash(), obj) for obj in objects]).write_pack_and_idx(
        repo.store.root / "pack", prefix
    )


def test_ordered_iterator_preserves_existing_hash_order(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for payload in (b"zeta\n", b"alpha\n", b"middle\n"):
        repo.store.write(BlobObject(payload))

    assert tuple(iter_object_ids(repo)) == all_object_ids(repo)
    assert tuple(iter_object_ids(repo)) == tuple(sorted(all_object_ids(repo)))


def test_unordered_iterator_groups_loose_before_packed_and_deduplicates(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    duplicate = BlobObject(b"duplicate loose and packed\n")
    packed_a = BlobObject(b"packed a\n")
    packed_b = BlobObject(b"packed b\n")
    loose_only = BlobObject(b"loose only\n")

    duplicate_oid = repo.store.write(duplicate)
    packed_a_oid = repo.store.write(packed_a)
    packed_b_oid = repo.store.write(packed_b)
    loose_oid = repo.store.write(loose_only)
    _pack(repo, duplicate, packed_a, packed_b)
    _loose_path(repo, packed_a_oid).unlink()
    _loose_path(repo, packed_b_oid).unlink()

    result = list(iter_object_ids(repo, unordered=True))
    assert len(result) == len(set(result)) == 4
    assert set(result) == {duplicate_oid, packed_a_oid, packed_b_oid, loose_oid}

    packed_positions = [result.index(packed_a_oid), result.index(packed_b_oid)]
    assert min(packed_positions) >= 2
    assert set(result[:2]) == {duplicate_oid, loose_oid}


def test_unordered_iterator_does_not_call_global_sorted_enumerator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"stream me\n"))

    def fail_all_shas():
        raise AssertionError("unordered enumeration must not call ObjectStore.all_shas")

    monkeypatch.setattr(repo.store, "all_shas", fail_all_shas)
    assert list(iter_object_ids(repo, unordered=True)) == [oid]


def test_unordered_filters_incidental_noncanonical_loose_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"real\n"))
    junk_dir = repo.store.root / "aa"
    junk_dir.mkdir(exist_ok=True)
    (junk_dir / "not-an-object").write_text("junk", encoding="utf-8")
    upper_dir = repo.store.root / "AB"
    upper_dir.mkdir(exist_ok=True)
    (upper_dir / ("c" * 62)).write_text("junk", encoding="utf-8")

    assert list(iter_object_ids(repo, unordered=True)) == [oid]


def test_cli_unordered_requires_batch_all_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store.write(BlobObject(b"one\n"))

    result = _run(repo, "--batch-check", "--unordered")
    assert result.returncode == 2
    assert b"--unordered requires --batch-all-objects" in result.stderr


def test_cli_unordered_batch_check_emits_each_object_once(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    duplicate = BlobObject(b"duplicate\n")
    packed = BlobObject(b"packed only\n")
    loose = BlobObject(b"loose only\n")
    duplicate_oid = repo.store.write(duplicate)
    packed_oid = repo.store.write(packed)
    loose_oid = repo.store.write(loose)
    _pack(repo, duplicate, packed)
    _loose_path(repo, packed_oid).unlink()

    result = _run(repo, "--batch-check", "--batch-all-objects", "--unordered")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    lines = result.stdout.splitlines()
    emitted = [line.split(b" ", 1)[0].decode("ascii") for line in lines]
    assert emitted == list(iter_object_ids(repo, unordered=True))
    assert len(emitted) == len(set(emitted)) == 3
    assert set(emitted) == {duplicate_oid, packed_oid, loose_oid}


def test_unordered_composes_with_custom_format_and_zero_framing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.store.write(BlobObject(b"first\n"))
    second = repo.store.write(BlobObject(b"second\n"))
    fmt = "%(objectname)|%(objecttype)|%(objectsize:disk)|%(rest)"

    result = _run(
        repo,
        f"--batch-check={fmt}",
        "--batch-all-objects",
        "--unordered",
        "-Z",
        input_data=b"ignored\0stdin\0",
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    records = [record for record in result.stdout.split(b"\0") if record]
    assert len(records) == 2
    assert {record.split(b"|", 1)[0].decode("ascii") for record in records} == {first, second}
    assert all(record.endswith(b"|") for record in records)


def test_unordered_batch_contents_preserve_binary_payloads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    payloads = {b"left\x00inside\n", b"right\nbytes\x00"}
    oids = {repo.store.write(BlobObject(payload)) for payload in payloads}

    result = _run(repo, "--batch", "--batch-all-objects", "--unordered")
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    expected = b"".join(
        format_batch_object(repo, oid, contents=True)
        for oid in iter_object_ids(repo, unordered=True)
    )
    assert result.stdout == expected
    assert set(iter_object_ids(repo, unordered=True)) == oids


def test_help_exposes_unordered_option(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(repo, "--help")
    assert result.returncode == 0
    assert b"--unordered" in result.stdout
