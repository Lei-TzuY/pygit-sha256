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


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("object:type count must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("object:type count must not batch-fetch"),
    )


def test_tree_filter_exempts_only_provided_head_not_reachable_commits(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=object:type=tree",
            "--missing=print-info",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == c3
    assert c1 not in lines
    assert c2 not in lines
    assert {_tree(repo, c1), _tree(repo, c2), _tree(repo, c3)} <= {
        line.split(None, 1)[0] for line in lines[1:]
    }
    assert all(
        repo.store.read(line.split(None, 1)[0]).type_name == b"tree"
        for line in lines[1:]
    )


def test_all_filter_exempts_each_ref_tip_not_common_ancestor(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, c2, c3) = _ordinary_three_commit_repo(tmp_path)
    repo.refs.set_branch("side", c2, message="test: side tip")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--all",
            "--filter=object:type=tree",
            "--missing=print-info",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert c3 in lines
    assert c2 in lines
    assert c1 not in lines
    assert sum(1 for line in lines if line in {c2, c3}) == 2


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("commit", "3"), ("tree", "4"), ("blob", "4")],
)
def test_object_type_count_matches_native_head_stream(
    tmp_path, monkeypatch, capsys, requested, expected
):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            f"--filter=object:type={requested}",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [expected]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("commit", "2"), ("tree", "3"), ("blob", "4")],
)
def test_object_type_count_boundary_max_count_matches_native(
    tmp_path, monkeypatch, capsys, requested, expected
):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--boundary",
            "--max-count=1",
            f"--filter=object:type={requested}",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [expected]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("commit", "2"), ("tree", "3"), ("blob", "3")],
)
def test_object_type_count_edge_boundary_range_preserves_edge_but_excludes_it(
    tmp_path, monkeypatch, capsys, requested, expected
):
    repo, (c1, _c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            "--max-count=1",
            f"--filter=object:type={requested}",
            "--missing=print-info",
            "--count",
            f"{c1}..{c3}",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [f"-{c1}", expected]


def test_object_type_blob_count_keeps_missing_record_but_does_not_count_it(
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
            "--filter=object:type=blob",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"?{tip_blob} path=f.txt type=blob",
        "1",
    ]
    assert read_promisor_state(repo.pygit_dir) == before
    assert tip


def test_object_type_tree_count_filters_promised_blob_without_fetch(
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
            "--filter=object:type=tree",
            "--missing=print-info",
            "--count",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == ["2"]
    assert tip_blob not in "\n".join(lines)
    assert read_promisor_state(repo.pygit_dir) == before
    assert _tree(repo, tip)
