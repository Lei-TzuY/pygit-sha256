from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.objects import CommitObject, TreeObject
from pygit.promisor import read_promisor_state
from pygit.remote import NativeObject
from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _tree_data(blob_oid: str, *, name: str = "f.txt") -> bytes:
    return b"100644 " + name.encode() + b"\x00" + bytes.fromhex(blob_oid)


def _commit_data(
    tree_oid: str,
    *,
    parent: str | None = None,
    timestamp: int = 1,
) -> bytes:
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


def _tree(repo: Repository, commit_sha: str) -> str:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("NUL blob:none traversal must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("NUL blob:none traversal must not batch-fetch"),
    )


def test_nul_blob_none_ordinary_omits_local_blob_and_keeps_tree_path(
    tmp_path, monkeypatch, capsys
):
    repo = Repository.init(str(tmp_path / "ordinary"))
    nested = repo.worktree / "dir\nname"
    nested.mkdir()
    (nested / "f.txt").write_text("payload\n", encoding="utf-8")
    repo.add(["dir\nname/f.txt"])
    commit_sha = repo.commit("tip", author="Test <test@example.com>")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    root = repo.store.read(commit.tree)
    assert isinstance(root, TreeObject)
    child_tree_sha = root.entries[0].sha
    child_tree = repo.store.read(child_tree_sha)
    assert isinstance(child_tree, TreeObject)
    blob_sha = child_tree.entries[0].sha

    capsys.readouterr()
    assert run_rev_list_disk_usage(
        ["--objects", "-z", "--filter=blob:none", "HEAD"]
    ) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{commit_sha}\0")
    assert commit.tree in out
    assert child_tree_sha in out
    assert blob_sha not in out
    assert "path=dir\nname\0" in out
    assert "path=dir\nname/f.txt\0" not in out
    assert "missing=yes\0" not in out


@pytest.mark.parametrize(
    "missing_args",
    [
        (),
        ("--missing=allow-promisor",),
        ("--missing=print",),
        ("--missing=print-info",),
    ],
)
def test_nul_blob_none_filters_promised_blob_without_fetch(
    tmp_path, monkeypatch, capsys, missing_args
):
    repo, _base, tip, _base_blob, tip_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "-z", "--filter=blob:none", *missing_args, "HEAD"]
    ) == 0

    out = capsys.readouterr().out
    assert tip in out
    assert _tree(repo, tip) in out
    assert tip_blob not in out
    assert "missing=yes\0" not in out
    assert "type=blob\0" not in out
    assert read_promisor_state(repo.pygit_dir) == before


def test_nul_blob_none_boundary_keeps_boundary_metadata_and_snapshot_trees(
    tmp_path, monkeypatch, capsys
):
    repo, base, tip, base_blob, tip_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "-z",
            "--boundary",
            "--max-count=1",
            "--filter=blob:none",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{tip}\0")
    assert f"{base}\0boundary=yes\0" in out
    assert _tree(repo, tip) in out
    assert _tree(repo, base) in out
    assert base_blob not in out
    assert tip_blob not in out
    assert "missing=yes\0" not in out
    assert read_promisor_state(repo.pygit_dir) == before


def test_nul_blob_none_preserves_nul_protocol_option_rejections(
    tmp_path, monkeypatch
):
    repo = Repository.init(str(tmp_path / "reject"))
    (repo.worktree / "f.txt").write_text("x\n", encoding="utf-8")
    repo.add(["f.txt"])
    repo.commit("tip", author="Test <test@example.com>")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="not compatible with --count"):
        run_rev_list_disk_usage(
            ["--objects", "-z", "--filter=blob:none", "--count", "HEAD"]
        )
    with pytest.raises(ValueError, match="only compatible with --objects"):
        run_rev_list_disk_usage(
            ["--objects-edge", "-z", "--filter=blob:none", "HEAD"]
        )
