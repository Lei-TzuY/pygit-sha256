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


def _ordinary_two_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index in (1, 2):
        path = f"f{index}.txt"
        (repo.worktree / path).write_text(f"{index}\n", encoding="utf-8")
        repo.add([path])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
                commit_date=str(index),
            )
        )
    return repo, tuple(commits)


def _tree(repo: Repository, commit_sha: str) -> str:
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    return commit.tree


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("object:type rev-list must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("object:type rev-list must not batch-fetch"),
    )


@pytest.mark.parametrize("requested", ["commit", "tree", "blob"])
def test_object_type_preserves_selected_commit_and_filters_snapshot_types(
    tmp_path, monkeypatch, capsys, requested
):
    repo, (_base, tip) = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            f"--filter=object:type={requested}",
            "--missing=print-info",
            "--max-count=1",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == tip
    assert len(lines[0]) == 64
    remaining = lines[1:]
    for line in remaining:
        oid = line.split(None, 1)[0]
        obj = repo.store.read(oid)
        assert obj.type_name.decode("ascii") == requested

    if requested == "commit":
        assert remaining == []
    elif requested == "tree":
        assert any(line.split(None, 1)[0] == _tree(repo, tip) for line in remaining)
    else:
        assert remaining


def test_object_type_tree_filters_boundary_commit_but_keeps_boundary_snapshot_tree(
    tmp_path, monkeypatch, capsys
):
    repo, (base, tip) = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=object:type=tree",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == tip
    assert f"-{base}" not in lines
    assert any(line.split(None, 1)[0] == _tree(repo, tip) for line in lines[1:])
    assert any(line.split(None, 1)[0] == _tree(repo, base) for line in lines[1:])
    assert all(
        repo.store.read(line.split(None, 1)[0]).type_name == b"tree"
        for line in lines[1:]
    )


def test_object_type_commit_keeps_boundary_commit_and_removes_snapshot_objects(
    tmp_path, monkeypatch, capsys
):
    repo, (base, tip) = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            "--filter=object:type=commit",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [tip, f"-{base}"]


def test_object_type_tree_preserves_explicit_object_edge(tmp_path, monkeypatch, capsys):
    repo, (base, tip) = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--filter=object:type=tree",
            "--missing=print-info",
            f"{base}..{tip}",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"-{base}"
    assert tip in lines
    assert any(line.split(None, 1)[0] == _tree(repo, tip) for line in lines)
    for line in lines:
        token = line.split(None, 1)[0]
        if token in {f"-{base}", tip}:
            continue
        assert repo.store.read(token).type_name == b"tree"


@pytest.mark.parametrize(
    ("requested", "expect_missing"),
    [("commit", False), ("tree", False), ("blob", True)],
)
def test_object_type_filters_promised_blob_by_promisor_kind_without_fetch(
    tmp_path, monkeypatch, capsys, requested, expect_missing
):
    repo, _base, tip, _base_blob, tip_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            f"--filter=object:type={requested}",
            "--missing=print-info",
            "--max-count=1",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == tip
    missing = [line for line in lines if line.startswith("?")]
    if expect_missing:
        assert missing == [f"?{tip_blob} path=f.txt type=blob"]
    else:
        assert missing == []
        assert tip_blob not in "\n".join(lines)
    assert read_promisor_state(repo.pygit_dir) == before


def test_object_type_plain_print_uses_promisor_kind_for_missing_filter(
    tmp_path, monkeypatch, capsys
):
    repo, _base, tip, _base_blob, tip_blob = _partial_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=object:type=blob",
            "--missing=print",
            "--max-count=1",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [tip, f"?{tip_blob}"]


def test_object_type_keeps_nul_and_tag_deliberately_deferred(tmp_path, monkeypatch):
    repo, _commits = _ordinary_two_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="with -z is not yet supported"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "-z",
                "--filter=object:type=tree",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )
    with pytest.raises(ValueError, match="annotated-tag traversal is not modelled"):
        run_rev_list_disk_usage(
            [
                "--objects",
                "--filter=object:type=tag",
                "--missing=allow-promisor",
                "HEAD",
            ]
        )
