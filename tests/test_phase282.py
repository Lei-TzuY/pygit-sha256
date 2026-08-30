from __future__ import annotations

import hashlib
import subprocess
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
            "blob-limit NUL classification must not single-fetch content"
        ),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail(
            "blob-limit NUL classification must not batch-fetch content"
        ),
    )


def _nul_fields(output: str):
    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    return fields


def test_plain_blob_limit_nul_filters_exact_threshold_local_blob(
    tmp_path, monkeypatch, capsys
):
    repo, commit_oid, tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--missing=allow-promisor",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    fields = _nul_fields(capsys.readouterr().out)
    assert commit_oid in fields
    assert tree_oid in fields
    assert blobs["small.bin"] in fields
    assert blobs["large.bin"] not in fields
    assert all(len(field) == 64 for field in fields)


def test_plain_blob_limit_nul_keeps_structured_path_for_surviving_blob(
    tmp_path, monkeypatch, capsys
):
    repo, _commit_oid, _tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--missing=allow-promisor",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert f"{blobs['small.bin']}\0path=small.bin\0" in output
    assert blobs["large.bin"] not in output


def test_plain_blob_limit_nul_count_uses_filtered_present_inventory(
    tmp_path, monkeypatch, capsys
):
    repo, _commit_oid, _tree_oid, _blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--missing=allow-promisor",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out == "3\n"


def test_plain_blob_limit_nul_trusted_small_promise_stays_missing(
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
            "-z",
            "--filter=blob:limit=18",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert f"{native_blob}\0missing=yes\0path=f.txt\0type=blob\0" in output
    assert read_promisor_state(repo.pygit_dir) == before


def test_plain_blob_limit_nul_filtered_promise_disappears_without_fetch(
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
            "-z",
            "--filter=blob:limit=17",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert native_blob not in output
    assert "missing=yes" not in output
    assert read_promisor_state(repo.pygit_dir) == before


def test_plain_blob_limit_nul_missing_then_count_order(tmp_path, monkeypatch, capsys):
    repo, native_blob, _payload_size = _partial_repo(tmp_path, trusted_size=17)
    _route_repo(monkeypatch, repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=18",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert output == f"{native_blob}\0missing=yes\0path=f.txt\0type=blob\02\n"


def test_plain_blob_limit_nul_missing_size_fails_before_output(
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
                "-z",
                "--filter=blob:limit=18",
                "--missing=print-info",
                "HEAD",
            ]
        )

    assert capsys.readouterr().out == ""


def test_plain_blob_limit_nul_omitted_output_composes_with_followup(
    tmp_path, monkeypatch, capsys
):
    repo, _commit_oid, _tree_oid, blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--filter-print-omitted",
            "--missing=allow-promisor",
            "--no-object-names",
            "HEAD",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert output.endswith(f"~{blobs['large.bin']}\n")


def test_plain_blob_limit_nul_keeps_existing_objects_edge_rejection(
    tmp_path, monkeypatch
):
    repo, _commit_oid, _tree_oid, _blobs = _ordinary_repo(tmp_path)
    _route_repo(monkeypatch, repo)

    with pytest.raises(ValueError, match="-z is only compatible with --objects"):
        run_rev_list_disk_usage(
            [
                "--objects-edge",
                "-z",
                "--filter=blob:limit=8",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )


def test_native_git_sha256_plain_blob_limit_nul_membership(tmp_path):
    repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "--object-format=sha256", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "small.bin").write_bytes(b"sss")
    (repo / "large.bin").write_bytes(b"LLLLLLLL")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "payloads"],
        check=True,
        capture_output=True,
    )

    def rev_parse(expr: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", expr],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    commit_oid = rev_parse("HEAD")
    tree_oid = rev_parse("HEAD^{tree}")
    small_oid = rev_parse("HEAD:small.bin")
    large_oid = rev_parse("HEAD:large.bin")
    output = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--objects",
            "-z",
            "--filter=blob:limit=8",
            "--no-object-names",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    ).stdout

    fields = [field for field in output.split(b"\0") if field]
    assert commit_oid.encode() in fields
    assert tree_oid.encode() in fields
    assert small_oid.encode() in fields
    assert large_oid.encode() not in fields
    assert all(len(field) == 64 for field in fields)
