from __future__ import annotations

import hashlib
from typing import Optional

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _ordinary_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "small.bin").write_bytes(b"sss")
    (repo.worktree / "large.bin").write_bytes(b"LLLLLLLL")
    repo.add(["small.bin", "large.bin"])
    commit_oid = repo.commit(
        "payloads",
        author_name="Test",
        author_email="test@example.com",
        commit_date="1",
    )
    commit = repo.store.read(commit_oid)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    blobs = {entry.name: entry.sha.lower() for entry in tree.entries}
    return repo, commit_oid, commit.tree.lower(), blobs


def _partial_repo(tmp_path, *, trusted_size: Optional[int]):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_data = b"promised payload\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = b"100644 f.txt\0" + bytes.fromhex(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\nmsg\n"
    ).encode()
    commit_oid = _native_oid("commit", commit_data)
    importer = PromisorFilteredNativeImporter(
        repo.store,
        {
            tree_oid: NativeObject("tree", tree_data, tree_oid),
            commit_oid: NativeObject("commit", commit_data, commit_oid),
        },
        remote="origin",
        filter_spec="blob:none",
    )
    local_commit = importer.import_oid(commit_oid)
    repo.refs.set_branch("main", local_commit, message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    if trusted_size is not None:
        update_promisor_state(repo.pygit_dir, sizes={blob_oid: trusted_size})
    return repo, blob_oid, len(blob_data)


def _route_repo(monkeypatch, repo):
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail(
            "plain blob-limit omitted classification must not single-fetch content"
        ),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail(
            "plain blob-limit omitted classification must not batch-fetch content"
        ),
    )


def test_plain_blob_limit_prints_exact_threshold_local_omission_last(
    tmp_path, monkeypatch, capsys
):
    repo, commit_oid, tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == f"~{blobs['large.bin']}"
    assert commit_oid in lines
    assert tree_oid in lines
    assert blobs["small.bin"] in lines
    assert blobs["large.bin"] not in lines[:-1]
    assert len(lines[-1][1:]) == 64


def test_plain_blob_limit_omitted_count_follows_omission(tmp_path, monkeypatch, capsys):
    repo, _commit_oid, _tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [f"~{blobs['large.bin']}", "3"]


def test_plain_known_small_promise_stays_in_missing_channel(
    tmp_path, monkeypatch, capsys
):
    repo, native_blob, payload_size = _partial_repo(tmp_path, trusted_size=17)
    assert payload_size == 17
    _route_repo(monkeypatch, repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:limit=18",
            "--filter-print-omitted",
            "--missing=print",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == f"?{native_blob}"
    assert not any(line.startswith("~") for line in lines)
    assert read_promisor_state(repo.pygit_dir) == before


def test_plain_known_small_promise_print_info_is_preserved(
    tmp_path, monkeypatch, capsys
):
    repo, native_blob, _payload_size = _partial_repo(tmp_path, trusted_size=17)
    _route_repo(monkeypatch, repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:limit=18",
            "--filter-print-omitted",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    missing = [line for line in lines if line.startswith(f"?{native_blob}")]
    assert len(missing) == 1
    assert "path=f.txt" in missing[0]
    assert "type=blob" in missing[0]


def test_plain_filtered_promise_refuses_omitted_identity_before_output(
    tmp_path, monkeypatch, capsys
):
    repo, native_blob, payload_size = _partial_repo(tmp_path, trusted_size=17)
    assert payload_size == 17
    _route_repo(monkeypatch, repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    with pytest.raises(
        RuntimeError,
        match="cannot expose unresolved filtered promisor blob.*local SHA-256 id",
    ):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--filter=blob:limit=17",
                "--filter-print-omitted",
                "--missing=print",
                "HEAD",
            ]
        )

    assert capsys.readouterr().out == ""
    assert read_promisor_state(repo.pygit_dir) == before
    assert len(native_blob) == 40


def test_plain_missing_promisor_size_keeps_strict_preflight_error(
    tmp_path, monkeypatch, capsys
):
    repo, _native_blob, _payload_size = _partial_repo(tmp_path, trusted_size=None)
    _route_repo(monkeypatch, repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    with pytest.raises(RuntimeError, match="persistent promisor size metadata is unavailable"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--filter=blob:limit=18",
                "--filter-print-omitted",
                "--missing=print",
                "HEAD",
            ]
        )
    assert capsys.readouterr().out == ""


def test_plain_blob_limit_omitted_nul_remains_explicitly_deferred(
    tmp_path, monkeypatch
):
    repo, _commit_oid, _tree_oid, _blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match="blob:limit with -z is not yet supported"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "-z",
                "--filter=blob:limit=8",
                "--filter-print-omitted",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )
