"""Phase 119 tests: exact raw object reads and cat-file byte fidelity."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.cat_file import format_batch_object, inspect_object, run_batch_commands
from pygit.objects import CommitObject, TreeObject
from pygit.objects.base import GitObject
from pygit.pack import PackReader, PackWriter


class _RawCommit(GitObject):
    """Commit-typed test object that preserves headers CommitObject does not model."""

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
        "x-phase119 preserved-extension\n"
        "\n"
        "byte-faithful commit\n"
    ).encode("utf-8")
    return _RawCommit(payload)


def _envelope(obj: GitObject) -> bytes:
    payload = obj.serialize()
    return obj.type_name + b" " + str(len(payload)).encode("ascii") + b"\0" + payload


def _batch_payload(oid: str, payload: bytes) -> bytes:
    return f"{oid} commit {len(payload)}\n".encode("ascii") + payload + b"\n"


def test_loose_raw_read_preserves_unmodeled_commit_headers(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw = _raw_commit(repo)
    oid = repo.store.write(raw)

    parsed = repo.store.read(oid)
    assert isinstance(parsed, CommitObject)
    assert b"gpgsig " not in parsed.serialize()
    assert b"x-phase119 " not in parsed.serialize()

    assert repo.store.read_store_bytes(oid) == _envelope(raw)

    record = inspect_object(repo, oid)
    assert record.oid == oid
    assert record.type_name == "commit"
    assert record.size == len(raw.payload)
    assert record.content == raw.payload
    assert format_batch_object(repo, oid, contents=True) == _batch_payload(
        oid, raw.payload
    )


def test_packed_raw_read_is_exact_and_does_not_recreate_loose_copy(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw = _raw_commit(repo)
    oid = repo.store.write(raw)
    pack_dir = repo.store.root / "pack"
    _pack_path, idx_path = PackWriter([(oid, raw)]).write_pack_and_idx(pack_dir)
    assert repo.store.delete(oid)
    assert not repo.store._path_for(oid).exists()

    reader = PackReader(idx_path)
    assert reader.read_store_bytes(oid) == _envelope(raw)
    assert repo.store.read_store_bytes(oid) == _envelope(raw)
    assert not repo.store._path_for(oid).exists()

    record = inspect_object(repo, oid)
    assert record.content == raw.payload
    assert record.size == len(raw.payload)
    assert not repo.store._path_for(oid).exists()


def test_batch_command_contents_preserves_packed_commit_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw = _raw_commit(repo)
    oid = raw.hash()
    _pack_path, _idx_path = PackWriter([(oid, raw)]).write_pack_and_idx(
        repo.store.root / "pack"
    )

    chunks = list(run_batch_commands(repo, [f"contents {oid}\n"]))
    assert chunks == [_batch_payload(oid, raw.payload)]
    assert b"gpgsig -----BEGIN PGP SIGNATURE-----" in chunks[0]
    assert b"x-phase119 preserved-extension" in chunks[0]


def test_raw_pack_reader_missing_oid_remains_none(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    raw = _raw_commit(repo)
    oid = raw.hash()
    _pack_path, idx_path = PackWriter([(oid, raw)]).write_pack_and_idx(
        repo.store.root / "pack"
    )

    reader = PackReader(idx_path)
    assert reader.read_store_bytes("f" * 64) is None
