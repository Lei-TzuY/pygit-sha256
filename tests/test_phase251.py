from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index, name in enumerate(("a.txt", "b.txt", "c.txt"), start=1):
        (repo.worktree / name).write_text(f"{index}\n", encoding="utf-8")
        repo.add([name])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    return repo, tuple(commits)


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(blob_oid: str) -> bytes:
    return b"100644 f.txt\x00" + bytes.fromhex(blob_oid)


def _commit_data(tree_oid: str, *, parent: str | None = None, timestamp: int = 1) -> bytes:
    parent_line = f"parent {parent}\n" if parent else ""
    return (
        f"tree {tree_oid}\n"
        f"{parent_line}"
        f"author Test <test@example.com> {timestamp} +0000\n"
        f"committer Test <test@example.com> {timestamp} +0000\n"
        "\nmsg\n"
    ).encode()


def _partial_two_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    base_blob = _native_oid("blob", b"base\n")
    base_tree_data = _tree_data(base_blob)
    base_tree = _native_oid("tree", base_tree_data)
    base_commit_data = _commit_data(base_tree, timestamp=1)
    base_commit = _native_oid("commit", base_commit_data)

    tip_blob = _native_oid("blob", b"tip\n")
    tip_tree_data = _tree_data(tip_blob)
    tip_tree = _native_oid("tree", tip_tree_data)
    tip_commit_data = _commit_data(tip_tree, parent=base_commit, timestamp=2)
    tip_commit = _native_oid("commit", tip_commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            base_tree: NativeObject("tree", base_tree_data, base_tree),
            base_commit: NativeObject("commit", base_commit_data, base_commit),
            tip_tree: NativeObject("tree", tip_tree_data, tip_tree),
            tip_commit: NativeObject("commit", tip_commit_data, tip_commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_base = importer.import_oid(base_commit)
    local_tip = importer.import_oid(tip_commit)
    repo.refs.set_branch("main", local_tip, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local_base, local_tip, base_blob, tip_blob


def _tree(repo: Repository, commit_oid: str) -> str:
    obj = repo.store.read(commit_oid)
    assert isinstance(obj, CommitObject)
    return obj.tree.lower()


def _records(raw: str):
    records = []
    current = None
    for token in raw.split("\0"):
        if not token:
            continue
        if "=" not in token:
            if current is not None:
                records.append(current)
            current = [token]
        else:
            assert current is not None
            current.append(token)
    if current is not None:
        records.append(current)
    return records


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("object:type -z must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("object:type -z must not batch-fetch"),
    )


def test_object_type_tree_nul_exempts_only_provided_head(tmp_path, monkeypatch, capsys):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "-z", "--filter=object:type=tree", "HEAD"]
    ) == 0

    records = _records(capsys.readouterr().out)
    oids = [record[0] for record in records]
    assert oids[0] == c3
    assert c1 not in oids
    assert c2 not in oids
    assert {_tree(repo, c1), _tree(repo, c2), _tree(repo, c3)} <= set(oids)
    for oid in oids[1:]:
        assert repo.store.read(oid).type_name == b"tree"


def test_object_type_tree_nul_filters_boundary_commit_but_keeps_snapshot_tree(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "-z",
            "--filter=object:type=tree",
            "HEAD",
        ]
    ) == 0

    records = _records(capsys.readouterr().out)
    assert records[0] == [c3]
    assert [c2, "boundary=yes"] not in records
    oids = {record[0] for record in records}
    assert _tree(repo, c3) in oids
    assert _tree(repo, c2) in oids
    assert c1 not in oids


def test_object_type_commit_nul_keeps_boundary_metadata(tmp_path, monkeypatch, capsys):
    repo, (_c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "-z",
            "--filter=object:type=commit",
            "HEAD",
        ]
    ) == 0

    assert _records(capsys.readouterr().out) == [[c3], [c2, "boundary=yes"]]


def test_object_type_tree_nul_filters_promised_blob_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, _base, tip, _base_blob, tip_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--max-count=1",
            "-z",
            "--filter=object:type=tree",
            "HEAD",
        ]
    ) == 0

    records = _records(capsys.readouterr().out)
    assert records[0] == [tip]
    assert [record[0] for record in records[1:]] == [_tree(repo, tip)]
    assert tip_blob not in "\0".join(field for record in records for field in record)
    assert read_promisor_state(repo.pygit_dir) == before


def test_object_type_blob_nul_print_info_keeps_native_missing_identity(
    tmp_path, monkeypatch, capsys
):
    repo, _base, tip, _base_blob, tip_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--max-count=1",
            "-z",
            "--filter=object:type=blob",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    records = _records(capsys.readouterr().out)
    assert records[0] == [tip]
    assert records[1] == [tip_blob, "missing=yes", "path=f.txt", "type=blob"]
    assert len(records[0][0]) == 64
    assert len(records[1][0]) == 40
    assert read_promisor_state(repo.pygit_dir) == before
