from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import BlobObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _ordinary_repo(tmp_path, sizes=(3, 8)):
    repo = Repository.init(str(tmp_path / "ordinary"))
    for index, size in enumerate(sizes, 1):
        path = f"f{index}.bin"
        (repo.worktree / path).write_bytes(bytes([64 + index]) * size)
        repo.add([path])
    head = repo.commit(
        "sizes",
        author_name="Test",
        author_email="test@example.com",
        commit_date="1",
    )
    return repo, head


def _local_blobs_from_output(repo, output: str):
    blobs = {}
    for line in output.splitlines():
        if not line:
            continue
        oid = line.split(None, 1)[0]
        if len(oid) != 64:
            continue
        obj = repo.store.read(oid)
        if isinstance(obj, BlobObject):
            blobs[len(obj)] = oid
    return blobs


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(blob_oid: str) -> bytes:
    return b"100644 f.txt\x00" + bytes.fromhex(blob_oid)


def _commit_data(tree_oid: str) -> bytes:
    return (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\nmsg\n"
    ).encode()


def _partial_blob_none_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob = _native_oid("blob", b"promised payload\n")
    tree_data = _tree_data(blob)
    tree = _native_oid("tree", tree_data)
    commit_data = _commit_data(tree)
    commit = _native_oid("commit", commit_data)

    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            tree: NativeObject("tree", tree_data, tree),
            commit: NativeObject("commit", commit_data, commit),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local = importer.import_oid(commit)
    repo.refs.set_branch("main", local, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, blob


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blob:limit must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob:limit must not batch-fetch"),
    )


def test_blob_limit_omits_blobs_at_or_above_threshold(tmp_path, monkeypatch, capsys):
    repo, head = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    assert run_rev_list_disk_usage(
        ["--objects", "--missing=print-info", head]
    ) == 0
    blobs = _local_blobs_from_output(repo, capsys.readouterr().out)
    assert set(blobs) == {3, 8}

    assert run_rev_list_disk_usage(
        ["--objects", "--filter=blob:limit=8", "--missing=print-info", head]
    ) == 0
    output = capsys.readouterr().out
    assert blobs[3] in output
    assert blobs[8] not in output

    assert run_rev_list_disk_usage(
        ["--objects", "--filter=blob:limit=9", "--missing=print-info", head]
    ) == 0
    output = capsys.readouterr().out
    assert blobs[3] in output
    assert blobs[8] in output


def test_blob_limit_binary_suffix_uses_kibibytes(tmp_path, monkeypatch, capsys):
    repo, head = _ordinary_repo(tmp_path, sizes=(1023, 1024))
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    assert run_rev_list_disk_usage(
        ["--objects", "--missing=print-info", head]
    ) == 0
    blobs = _local_blobs_from_output(repo, capsys.readouterr().out)

    assert run_rev_list_disk_usage(
        ["--objects", "--filter=blob:limit=1k", "--missing=print-info", head]
    ) == 0
    output = capsys.readouterr().out
    assert blobs[1023] in output
    assert blobs[1024] not in output


def test_blob_limit_count_counts_only_surviving_present_objects(
    tmp_path, monkeypatch, capsys
):
    repo, head = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    args = ["--objects", "--filter=blob:limit=8", "--missing=print-info", head]
    assert run_rev_list_disk_usage(args) == 0
    surviving = [line for line in capsys.readouterr().out.splitlines() if line]

    assert run_rev_list_disk_usage([*args[:-1], "--count", args[-1]]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [str(len(surviving))]


def test_blob_limit_zero_omits_every_local_blob(tmp_path, monkeypatch, capsys):
    repo, head = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    assert run_rev_list_disk_usage(
        ["--objects", "--filter=blob:limit=0", "--missing=print-info", head]
    ) == 0
    output = capsys.readouterr().out
    assert _local_blobs_from_output(repo, output) == {}
    assert head in output


def test_blob_limit_refuses_unresolved_promised_blob_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, native_blob = _partial_blob_none_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match="persistent promisor size metadata is unavailable"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--filter=blob:limit=1k",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )

    assert native_blob in read_promisor_state(repo.pygit_dir).objects
    assert read_promisor_state(repo.pygit_dir) == before


def test_blob_limit_rejects_invalid_or_deferred_forms(tmp_path, monkeypatch):
    repo, head = _ordinary_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match=r"requires <n>\[kmg\]"):
        run_rev_list_disk_usage(
            ["--objects", "--filter=blob:limit=1t", "--missing=print-info", head]
        )
    with pytest.raises(ValueError, match="with -z is not yet supported"):
        run_rev_list_disk_usage(
            ["--objects", "-z", "--filter=blob:limit=8", "--missing=print-info", head]
        )
    with pytest.raises(ValueError, match="with --filter-print-omitted is not yet supported"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--filter=blob:limit=8",
                "--filter-print-omitted",
                "--missing=print-info",
                head,
            ]
        )
