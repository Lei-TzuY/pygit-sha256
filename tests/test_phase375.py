from __future__ import annotations

import stat
import subprocess
import zlib
from pathlib import Path

import pytest

import pygit.durable_object_store as durable_store
from pygit.objects import BlobObject
from pygit.repo import Repository


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _assert_exact_stream(path: Path, expected: bytes) -> None:
    raw = path.read_bytes()
    decoder = zlib.decompressobj()
    output = decoder.decompress(raw)
    assert decoder.eof is True
    assert decoder.unused_data == b""
    assert decoder.unconsumed_tail == b""
    assert output == expected


def _replace_spy(monkeypatch: pytest.MonkeyPatch):
    real_replace = durable_store.os.replace
    calls: list[tuple[Path, Path]] = []

    def record(src, dst) -> None:
        calls.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(durable_store.os, "replace", record)
    return calls


def test_trailing_garbage_existing_object_is_atomically_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase375-trailing-garbage")
    oid = repo.store.write(blob)
    path = repo.store._path_for(oid)
    path.write_bytes(path.read_bytes() + b"trailing-garbage")
    replacements = _replace_spy(monkeypatch)

    assert repo.store.write(blob) == oid

    assert len(replacements) == 1
    _assert_exact_stream(path, blob._build_store_bytes())


def test_concatenated_zlib_stream_is_not_accepted_as_existing_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase375-concatenated")
    oid = repo.store.write(blob)
    path = repo.store._path_for(oid)
    path.write_bytes(path.read_bytes() + zlib.compress(b"second-stream"))
    replacements = _replace_spy(monkeypatch)

    assert repo.store.write(blob) == oid

    assert len(replacements) == 1
    _assert_exact_stream(path, blob._build_store_bytes())


def test_truncated_existing_zlib_stream_is_atomically_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase375-truncated")
    oid = repo.store.write(blob)
    path = repo.store._path_for(oid)
    raw = path.read_bytes()
    assert len(raw) > 2
    path.write_bytes(raw[:-2])
    replacements = _replace_spy(monkeypatch)

    assert repo.store.write(blob) == oid

    assert len(replacements) == 1
    _assert_exact_stream(path, blob._build_store_bytes())


def test_existing_stream_that_expands_beyond_expected_is_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"phase375-output-bound")
    oid = repo.store.write(blob)
    path = repo.store._path_for(oid)
    expected = blob._build_store_bytes()
    path.write_bytes(zlib.compress(expected + b"x" * (2 * 1024 * 1024)))
    replacements = _replace_spy(monkeypatch)

    assert repo.store.write(blob) == oid

    assert len(replacements) == 1
    _assert_exact_stream(path, expected)


def test_existing_validation_caps_each_decompress_output_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blob = BlobObject(b"z" * (3 * 1024 * 1024))
    oid = repo.store.write(blob)
    real_factory = durable_store.zlib.decompressobj
    limits: list[int] = []

    class DecoderProxy:
        def __init__(self) -> None:
            self.inner = real_factory()

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            limits.append(max_length)
            return self.inner.decompress(data, max_length)

        @property
        def eof(self):
            return self.inner.eof

        @property
        def unused_data(self):
            return self.inner.unused_data

        @property
        def unconsumed_tail(self):
            return self.inner.unconsumed_tail

    monkeypatch.setattr(durable_store.zlib, "decompressobj", DecoderProxy)

    assert repo.store.write(blob) == oid
    assert limits
    assert all(0 < limit <= durable_store._OUTPUT_CHUNK for limit in limits)
    assert len(limits) >= 3


def test_native_git_strict_fsck_rejects_loose_object_trailing_garbage(
    tmp_path: Path,
) -> None:
    native = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", str(native)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    payload = b"phase375-native-trailing-garbage\n"
    oid = subprocess.run(
        ["git", "-C", str(native), "hash-object", "-w", "--stdin"],
        input=payload,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.decode("ascii").strip()
    path = native / ".git" / "objects" / oid[:2] / oid[2:]
    # Git 2.55 writes loose objects read-only. The fixture must deliberately
    # corrupt it, so add owner-write permission before appending test garbage.
    path.chmod(path.stat().st_mode | stat.S_IWUSR)
    path.write_bytes(path.read_bytes() + b"junk-after-zlib-stream")

    result = subprocess.run(
        ["git", "-C", str(native), "fsck", "--strict"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    diagnostic = (result.stdout + result.stderr).decode("utf-8", "replace").lower()

    assert len(oid) == 64
    assert result.returncode != 0
    assert "garbage at end of loose object" in diagnostic or "corrupt loose object" in diagnostic
