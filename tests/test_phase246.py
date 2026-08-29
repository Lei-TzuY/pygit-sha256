from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


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


def _partial_range_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
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


def _tree(repo: Repository, commit_sha: str) -> str:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blob:none rev-list filter must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob:none rev-list filter must not batch-fetch"),
    )


def test_blob_none_allow_promisor_omits_promised_blob_without_fetch(tmp_path, monkeypatch, capsys):
    repo, _base, tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--filter=blob:none", "--missing=allow-promisor", "HEAD"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert any(line == tip for line in lines)
    assert any(line.split(None, 1)[0] == _tree(repo, tip) for line in lines)
    assert tip_blob not in "\n".join(lines)
    assert not any(line.startswith("?") for line in lines)
    assert read_promisor_state(repo.pygit_dir) == before


def test_blob_none_print_info_filters_missing_blob_channel(tmp_path, monkeypatch, capsys):
    repo, _base, tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--filter=blob:none", "--missing=print-info", "HEAD"]
    ) == 0

    output = capsys.readouterr().out
    assert tip in output
    assert _tree(repo, tip) in output
    assert tip_blob not in output
    assert "type=blob" not in output


def test_blob_none_objects_edge_boundary_preserves_sha256_edge(tmp_path, monkeypatch, capsys):
    repo, base, tip, base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            "--filter=blob:none",
            "--missing=print-info",
            f"{base}..{tip}",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"-{base}"
    assert lines.count(f"-{base}") == 1
    assert tip in lines
    assert any(line.split(None, 1)[0] == _tree(repo, tip) for line in lines)
    joined = "\n".join(lines)
    assert base_blob not in joined
    assert tip_blob not in joined


def test_blob_none_rejects_unmodelled_count_and_filter(tmp_path, monkeypatch):
    repo, _base, _tip, _base_blob, _tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="count"):
        run_rev_list_disk_usage(
            ["--objects", "--filter=blob:none", "--missing=allow-promisor", "--count", "HEAD"]
        )

    with pytest.raises(ValueError, match="currently supports --filter=blob:none"):
        run_rev_list_disk_usage(
            ["--objects", "--filter=tree:1", "--missing=allow-promisor", "HEAD"]
        )
