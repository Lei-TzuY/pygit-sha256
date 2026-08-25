"""Phase 58 tests: typed and stdin ``hash-object`` plumbing."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from pygit import Repository, hash_object_data, object_envelope, write_object_data
from pygit.hash_object import hash_path
from pygit.launcher import _run_hash_object
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeObject
from pygit.objects.base import HASH_ALGO


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def test_blob_hash_matches_existing_object_model() -> None:
    data = b"hello\x00world\n"
    assert hash_object_data(data) == BlobObject(data).hash()
    expected = hashlib.new(HASH_ALGO, b"blob 12\x00" + data).hexdigest()
    assert hash_object_data(data) == expected
    assert object_envelope(data) == b"blob 12\x00" + data


def test_hash_path_without_repository(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"payload")
    assert hash_path(path) == BlobObject(b"payload").hash()


def test_write_is_idempotent_and_readable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    oid = write_object_data(repo, b"stored\n")
    assert write_object_data(repo, b"stored\n") == oid
    obj = repo.store.read(oid)
    assert isinstance(obj, BlobObject)
    assert obj.data == b"stored\n"


def test_native_typed_payloads_hash_and_write(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ident = Identity("A", "a@example.com", timestamp=1, timezone="+0000")

    tree = TreeObject([])
    tree_oid = write_object_data(repo, tree.serialize(), "tree")
    assert isinstance(repo.store.read(tree_oid), TreeObject)

    commit = CommitObject(tree=tree_oid, author=ident, committer=ident, message="m")
    commit_oid = write_object_data(repo, commit.serialize(), "commit")
    assert isinstance(repo.store.read(commit_oid), CommitObject)

    tag = TagObject(
        target_sha=commit_oid,
        target_type=b"commit",
        tag_name="v1",
        tagger=ident,
        message="tag",
    )
    tag_oid = write_object_data(repo, tag.serialize(), "tag")
    assert isinstance(repo.store.read(tag_oid), TagObject)


def test_malformed_structured_payload_is_rejected_before_write(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    before = set(repo.store.all_shas())
    with pytest.raises(ValueError, match="invalid commit payload"):
        write_object_data(repo, b"this is not a commit", "commit")
    assert set(repo.store.all_shas()) == before

    with pytest.raises(ValueError, match="unsupported object type"):
        hash_object_data(b"x", "mystery")


def test_cli_multiple_files_preserve_argument_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    assert _run_hash_object([str(first), str(second)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        BlobObject(b"one").hash(),
        BlobObject(b"two").hash(),
    ]


def test_cli_stdin_hashes_raw_bytes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = b"binary\x00input\n"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8"))
    assert _run_hash_object(["--stdin"]) == 0
    assert capsys.readouterr().out.strip() == BlobObject(payload).hash()


def test_cli_stdin_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    first = tmp_path / "one file.txt"
    second = tmp_path / "two.txt"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{first}\n{second}\n"))

    assert _run_hash_object(["--stdin-paths"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        BlobObject(b"one").hash(),
        BlobObject(b"two").hash(),
    ]


def test_cli_write_and_typed_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    payload = TreeObject([]).serialize()
    source = repo.worktree / "tree.raw"
    source.write_bytes(payload)

    assert _run_hash_object(["-w", "-t", "tree", "tree.raw"]) == 0
    oid = capsys.readouterr().out.strip()
    assert isinstance(repo.store.read(oid), TreeObject)


def test_cli_write_requires_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "x"
    source.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="not a pygit repository"):
        _run_hash_object(["-w", str(source)])


def test_cli_rejects_conflicting_or_missing_sources() -> None:
    with pytest.raises(SystemExit):
        _run_hash_object(["--stdin", "file"])
    with pytest.raises(SystemExit):
        _run_hash_object([])
