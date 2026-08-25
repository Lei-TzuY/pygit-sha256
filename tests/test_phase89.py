"""Phase 89 tests: ``cat-file --batch-all-objects`` enumeration."""

from __future__ import annotations

import subprocess
import sys
import zlib
from pathlib import Path

from pygit import Repository, all_object_ids, batch_all_objects, format_batch_object
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


def _pack(repo: Repository, *objects: BlobObject, prefix: str = "phase89") -> None:
    pairs = [(obj.hash(), obj) for obj in objects]
    PackWriter(pairs).write_pack_and_idx(repo.store.root / "pack", prefix)


def test_all_object_ids_is_sorted_deduplicated_and_filters_junk(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = BlobObject(b"first\n")
    second = BlobObject(b"second\n")
    first_oid = repo.store.write(first)
    second_oid = repo.store.write(second)
    _pack(repo, first, second)
    _loose_path(repo, second_oid).unlink()

    junk_dir = repo.store.root / "aa"
    junk_dir.mkdir(exist_ok=True)
    (junk_dir / "not-an-object").write_text("junk", encoding="utf-8")
    upper_dir = repo.store.root / "AB"
    upper_dir.mkdir(exist_ok=True)
    (upper_dir / ("c" * 62)).write_text("junk", encoding="utf-8")

    assert all_object_ids(repo) == tuple(sorted((first_oid, second_oid)))
    assert repo.store.read(second_oid).data == b"second\n"


def test_batch_all_objects_api_supports_default_custom_and_contents(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    left = repo.store.write(BlobObject(b"left\x00payload\n"))
    right = repo.store.write(BlobObject(b"right\n"))
    ordered = tuple(sorted((left, right)))

    assert list(batch_all_objects(repo)) == [format_batch_object(repo, oid) for oid in ordered]

    fmt = "%(objectname)|%(objecttype)|%(objectsize)|%(rest)"
    custom = list(batch_all_objects(repo, format_string=fmt))
    assert custom == [format_batch_object(repo, oid, format_string=fmt) for oid in ordered]
    assert all(chunk.endswith(b"|\n") for chunk in custom)

    assert list(batch_all_objects(repo, contents=True)) == [
        format_batch_object(repo, oid, contents=True) for oid in ordered
    ]


def test_batch_all_objects_includes_unreachable_and_packed_only_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    loose = BlobObject(b"loose unreachable\n")
    packed = BlobObject(b"packed only\n")
    loose_oid = repo.store.write(loose)
    packed_oid = repo.store.write(packed)
    _pack(repo, packed)
    _loose_path(repo, packed_oid).unlink()

    assert all_object_ids(repo) == tuple(sorted((loose_oid, packed_oid)))
    assert list(batch_all_objects(repo)) == [
        format_batch_object(repo, oid) for oid in sorted((loose_oid, packed_oid))
    ]


def test_corrupt_canonical_loose_object_uses_existing_missing_record(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = repo.store.write(BlobObject(b"original\n"))
    _loose_path(repo, oid).write_bytes(zlib.compress(b"blob 7\x00changed"))

    assert all_object_ids(repo) == (oid,)
    assert list(batch_all_objects(repo)) == [f"{oid} missing\n".encode("ascii")]


def test_cli_batch_check_all_objects_ignores_stdin(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.store.write(BlobObject(b"one\n"))
    second = repo.store.write(BlobObject(b"two\n"))
    ordered = tuple(sorted((first, second)))

    result = _run(
        repo,
        "--batch-check",
        "--batch-all-objects",
        input_data=b"definitely-missing\nHEAD\n",
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    assert result.stdout == b"".join(format_batch_object(repo, oid) for oid in ordered)


def test_cli_batch_all_objects_custom_format_rest_and_buffer(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.store.write(BlobObject(b"alpha\n"))
    second = repo.store.write(BlobObject(b"beta\n"))
    ordered = tuple(sorted((first, second)))
    fmt = "%(objectname)|%(objecttype)|%(objectsize)|%(rest)"

    result = _run(
        repo,
        f"--batch-check={fmt}",
        "--batch-all-objects",
        "--buffer",
        input_data=b"ignored auxiliary data\n",
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == b"".join(format_batch_object(repo, oid, format_string=fmt) for oid in ordered)
    assert all(line.endswith(b"|") for line in result.stdout.splitlines())


def test_cli_batch_all_objects_emits_raw_contents(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.store.write(BlobObject(b"first\x00payload\n"))
    second = repo.store.write(BlobObject(b"second payload\n"))
    ordered = tuple(sorted((first, second)))

    result = _run(repo, "--batch", "--batch-all-objects", input_data=b"ignored\n")

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout == b"".join(format_batch_object(repo, oid, contents=True) for oid in ordered)


def test_cli_batch_command_all_objects_is_metadata_only_and_ignores_commands(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.store.write(BlobObject(b"first\n"))
    second = repo.store.write(BlobObject(b"second\n"))
    ordered = tuple(sorted((first, second)))

    result = _run(
        repo,
        "--batch-command",
        "--batch-all-objects",
        input_data=b"wat this-would-normally-fail\ncontents missing\n",
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    assert result.stdout == b"".join(format_batch_object(repo, oid) for oid in ordered)


def test_cli_batch_all_objects_validates_mode_and_positional_usage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.store.write(BlobObject(b"one\n"))

    no_mode = _run(repo, "-t", "--batch-all-objects", "HEAD")
    assert no_mode.returncode == 2
    assert b"--batch-all-objects requires" in no_mode.stderr

    positional = _run(repo, "--batch-check", "--batch-all-objects", "HEAD")
    assert positional.returncode == 2
    assert b"batch modes read object names or commands from stdin" in positional.stderr


def test_empty_repository_all_object_enumeration_is_clean(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert all_object_ids(repo) == ()
    assert list(batch_all_objects(repo)) == []

    result = _run(repo, "--batch-check", "--batch-all-objects", input_data=b"ignored\n")
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""
