"""Phase 120 tests: byte-faithful single-object ``cat-file -p`` output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import BlobObject, TreeEntry, TreeObject
from pygit.objects.base import GitObject
from pygit.pack import PackWriter


class _RawCommit(GitObject):
    """Commit-typed object carrying headers the parsed model does not preserve."""

    type_name = b"commit"

    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload

    def serialize(self) -> bytes:
        return self.payload

    def deserialize(self, data: bytes) -> None:
        self.payload = data


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _raw_commit(repo: Repository) -> _RawCommit:
    tree_oid = repo.store.write(TreeObject([]))
    payload = (
        f"tree {tree_oid}\n"
        "author A <a@example.com> 1 +0000\n"
        "committer A <a@example.com> 1 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " signed-line\n"
        " -----END PGP SIGNATURE-----\n"
        "x-phase120 preserve-me\n"
        "\n"
        "exact pretty payload\n"
    ).encode("utf-8")
    return _RawCommit(payload)


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_cat_file_pretty_preserves_exact_loose_commit_payload(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw = _raw_commit(repo)
    oid = repo.store.write(raw)

    result = _run(repo, "cat-file", "-p", oid)

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout == raw.payload
    assert b"gpgsig -----BEGIN PGP SIGNATURE-----" in result.stdout
    assert b"x-phase120 preserve-me" in result.stdout


def test_cat_file_pretty_preserves_packed_only_commit_without_materializing_loose(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    raw = _raw_commit(repo)
    oid = raw.hash()
    PackWriter([(oid, raw)]).write_pack_and_idx(repo.store.root / "pack")
    assert not repo.store._path_for(oid).exists()

    result = _run(repo, "cat-file", "--pretty", oid)

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == raw.payload
    assert not repo.store._path_for(oid).exists()


def test_cat_file_pretty_blob_remains_binary_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    payload = b"binary\x00payload\xff\n"
    oid = repo.store.write(BlobObject(payload))

    result = _run(repo, "cat-file", "-p", oid)

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == payload


def test_cat_file_pretty_tree_keeps_human_readable_listing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob_oid = repo.store.write(BlobObject(b"hello"))
    tree_oid = repo.store.write(TreeObject([TreeEntry("100644", "hello.txt", blob_oid)]))

    result = _run(repo, "cat-file", "-p", tree_oid)

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == f"100644 blob {blob_oid}\thello.txt\n".encode("ascii")
