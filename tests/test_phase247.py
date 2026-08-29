from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
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


def _partial_single_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "partial"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_data = b"promised\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = _tree_data(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_data = _commit_data(tree_oid)
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
    repo.refs.set_branch("main", local_commit, message="test: partial head")
    repo.refs.set_head_symbolic("main", message="test: partial head")
    return repo, blob_oid


def _ordinary_three_commit_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "ordinary"))
    commits = []
    for index, text in enumerate(("one\n", "two\n", "three\n"), start=1):
        (repo.worktree / "f.txt").write_text(text, encoding="utf-8")
        repo.add(["f.txt"])
        commits.append(
            repo.commit(
                f"c{index}",
                author_name="Test",
                author_email="test@example.com",
            )
        )
    return repo, tuple(commits)


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("blob:none count must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("blob:none count must not batch-fetch"),
    )


def test_blob_none_count_removes_present_local_blob(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path / "local"))
    (repo.worktree / "f.txt").write_text("local\n", encoding="utf-8")
    repo.add(["f.txt"])
    repo.commit("local", author_name="Test", author_email="test@example.com")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:none",
            "--missing=allow-promisor",
            "--count",
            "HEAD",
        ]
    ) == 0

    # Native Git counts the filtered object stream: one commit + one tree.
    assert capsys.readouterr().out.splitlines() == ["2"]


@pytest.mark.parametrize("missing_mode", ["allow-promisor", "print", "print-info"])
def test_blob_none_count_filters_promised_blob_without_fetch(
    tmp_path, monkeypatch, capsys, missing_mode
):
    repo, native_blob = _partial_single_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--filter=blob:none",
            f"--missing={missing_mode}",
            "--count",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines == ["2"]
    assert native_blob not in "\n".join(lines)
    assert not any(line.startswith("?") for line in lines)
    assert read_promisor_state(repo.pygit_dir) == before


def test_blob_none_count_objects_edge_boundary_excludes_edge_and_counts_limit_boundary(
    tmp_path, monkeypatch, capsys
):
    repo, (c1, _c2, c3) = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            "--filter=blob:none",
            "--missing=print-info",
            "--max-count=1",
            "--count",
            f"{c1}..{c3}",
        ]
    ) == 0

    # c1 is an explicit object edge and remains advertised but excluded from
    # the integer.  max-count introduces c2 as a distinct boundary which is a
    # present commit and therefore counts, along with c3 and both snapshot trees.
    assert capsys.readouterr().out.splitlines() == [f"-{c1}", "4"]


def test_blob_none_count_reverse_front_boundary_is_not_misclassified_as_edge(
    tmp_path, monkeypatch, capsys
):
    repo, _commits = _ordinary_three_commit_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            "--reverse",
            "--filter=blob:none",
            "--missing=print-info",
            "--max-count=1",
            "--count",
            "HEAD",
        ]
    ) == 0

    # There is no explicit exclusion edge here. Reverse presentation can place
    # the limit-induced boundary first in the uncounted stream, but it remains a
    # selected present boundary object and must contribute to the count.
    assert capsys.readouterr().out.splitlines() == ["4"]
