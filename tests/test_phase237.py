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


def _tree_data(entries):
    return b"".join(
        b"100644 " + name.encode() + b"\x00" + bytes.fromhex(oid)
        for name, oid in sorted(entries.items())
    )


def _commit_data(tree_oid: str) -> bytes:
    return (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\nprint info"
    ).encode()


def _partial_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    blobs = {
        "plain.txt": b"plain\n",
        "space name.txt": b"space\n",
    }
    blob_oids = {name: _native_oid("blob", data) for name, data in blobs.items()}
    tree_data = _tree_data(blob_oids)
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
    return repo, local_commit, blob_oids


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("print-info must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("print-info must not batch-fetch"),
    )


def test_rev_list_print_info_reports_native_promises_without_fetch(
    tmp_path, monkeypatch, capsys
):
    repo, local_commit, blob_oids = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)

    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()
    assert run_rev_list_disk_usage(
        ["--objects", "--missing=print-info", "HEAD"]
    ) == 0
    after = read_promisor_state(repo.pygit_dir)

    lines = capsys.readouterr().out.splitlines()
    assert before == after
    assert lines[0] == local_commit
    assert len(lines[0]) == 64
    root_tree = lines[1].split(" ", 1)[0]
    assert len(root_tree) == 64
    assert all(ch in "0123456789abcdef" for ch in root_tree)

    missing = lines[2:]
    assert missing == [
        f"?{blob_oids['plain.txt']} path=plain.txt type=blob",
        f'?{blob_oids["space name.txt"]} path="space name.txt" type=blob',
    ]
    assert all(len(line.split(" ", 1)[0]) == 41 for line in missing)
    assert all(len(line.split(" ", 1)[0][1:]) == 40 for line in missing)


def test_rev_list_print_info_no_object_names_keeps_missing_metadata(
    tmp_path, monkeypatch, capsys
):
    repo, _, blob_oids = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects",
            "--no-object-names",
            "--missing=print-info",
            "--max-count=1",
            "HEAD",
        ]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    present = [line for line in lines if not line.startswith("?")]
    missing = [line for line in lines if line.startswith("?")]
    assert len(present) == 2
    assert all(len(line) == 64 for line in present)
    assert missing == [
        f"?{blob_oids['plain.txt']} path=plain.txt type=blob",
        f'?{blob_oids["space name.txt"]} path="space name.txt" type=blob',
    ]


def test_rev_list_print_info_ordinary_repo_stays_sha256_only(
    tmp_path, monkeypatch, capsys
):
    repo = Repository.init(str(tmp_path / "ordinary"))
    (repo.worktree / "a.txt").write_text("alpha\n", encoding="utf-8")
    repo.add(["a.txt"])
    commit = repo.commit(
        "ordinary",
        author_name="Test",
        author_email="test@example.com",
    )
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--missing=print-info", "HEAD"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == commit
    assert len(lines) == 3
    assert not any(line.startswith("?") for line in lines)
    assert all(len(line.split(" ", 1)[0]) == 64 for line in lines)


@pytest.mark.parametrize(
    "args, message",
    [
        (
            ["--objects", "--boundary", "--missing=print-info", "HEAD"],
            "--boundary is not yet supported",
        ),
        (
            ["--objects-edge", "--missing=print-info", "HEAD"],
            "--objects-edge is not yet supported",
        ),
        (
            ["--objects", "--count", "--missing=print-info", "HEAD"],
            "--count is not yet supported",
        ),
    ],
)
def test_rev_list_print_info_rejects_unmodelled_presentation_modes(args, message):
    with pytest.raises(ValueError, match=message):
        run_rev_list_disk_usage(args)


def test_rev_list_missing_print_remains_explicitly_unsupported():
    with pytest.raises(ValueError, match="currently supports"):
        run_rev_list_disk_usage(["--objects", "--missing=print", "HEAD"])
