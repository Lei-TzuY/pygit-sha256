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


def _tree_data(blob_oid: str, path: str = "f.txt") -> bytes:
    return b"100644 " + path.encode() + b"\x00" + bytes.fromhex(blob_oid)


def _commit_data(tree_oid: str, *, message: str, parent: str | None = None, timestamp: int = 1) -> bytes:
    parent_line = f"parent {parent}\n" if parent is not None else ""
    return (
        f"tree {tree_oid}\n"
        f"{parent_line}"
        f"author Test <test@example.com> {timestamp} +0000\n"
        f"committer Test <test@example.com> {timestamp} +0000\n"
        f"\n{message}"
    ).encode()


def _partial_repo(tmp_path, *, path: str = "f.txt"):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_oid = _native_oid("blob", b"payload\n")
    tree_data = _tree_data(blob_oid, path)
    tree_oid = _native_oid("tree", tree_data)
    commit_data = _commit_data(tree_oid, message="tip")
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
    return repo, local_commit, blob_oid


def _partial_range_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "range"))
    repo.add_remote("origin", "https://example.test/repo.git")

    objects: dict[str, NativeObject] = {}
    parent = None
    commits: list[tuple[str, str]] = []
    for timestamp, label in enumerate(("base", "tip"), start=1):
        blob_oid = _native_oid("blob", f"{label}\n".encode())
        tree_data = _tree_data(blob_oid)
        tree_oid = _native_oid("tree", tree_data)
        commit_data = _commit_data(tree_oid, message=label, parent=parent, timestamp=timestamp)
        commit_oid = _native_oid("commit", commit_data)
        objects[tree_oid] = NativeObject("tree", tree_data, tree_oid)
        objects[commit_oid] = NativeObject("commit", commit_data, commit_oid)
        commits.append((commit_oid, blob_oid))
        parent = commit_oid

    importer = PromisorFilteredNativeImporter(
        repo.store,
        objects,
        remote="origin",
        filter_spec="blob:none",
    )
    local = [importer.import_oid(commit_oid) for commit_oid, _blob in commits]
    repo.refs.set_branch("main", local[-1], message="test: partial tip")
    repo.refs.set_head_symbolic("main", message="test: partial tip")
    return repo, local[0], local[1], commits[-1][1]


def _tree(repo: Repository, commit_oid: str) -> str:
    commit = repo.store.read(commit_oid)
    assert isinstance(commit, CommitObject)
    return commit.tree


def _disable_fetch(monkeypatch):
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("NUL traversal must not single-fetch"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("NUL traversal must not batch-fetch"),
    )


def test_rev_list_z_print_info_marks_missing_and_preserves_sha_domains(tmp_path, monkeypatch, capsys):
    repo, commit, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "-z", "--missing=print-info", "HEAD"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{commit}\0")
    assert f"{_tree(repo, commit)}\0path=\0" in out
    assert f"{native_blob}\0missing=yes\0path=f.txt\0type=blob\0" in out
    assert f"?{native_blob}" not in out
    assert "\n" not in out
    assert read_promisor_state(repo.pygit_dir) == before


def test_rev_list_z_plain_print_uses_only_missing_metadata(tmp_path, monkeypatch, capsys):
    repo, _commit, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "-z", "--missing=print", "HEAD"]) == 0

    out = capsys.readouterr().out
    assert f"{native_blob}\0missing=yes\0" in out
    assert f"{native_blob}\0missing=yes\0path=" not in out
    assert "type=blob\0" not in out


def test_rev_list_z_boundary_uses_boundary_metadata_not_text_prefix(tmp_path, monkeypatch, capsys):
    repo, base, tip, native_tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        ["--objects", "--boundary", "-z", "--missing=print-info", f"{base}..{tip}"]
    ) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{tip}\0{base}\0boundary=yes\0")
    assert f"-{base}" not in out
    assert f"{native_tip_blob}\0missing=yes\0path=f.txt\0type=blob\0" in out


def test_rev_list_z_print_info_emits_path_verbatim(tmp_path, monkeypatch, capsys):
    repo, _commit, native_blob = _partial_repo(tmp_path, path="line\nbreak.txt")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "-z", "--missing=print-info", "HEAD"]) == 0

    out = capsys.readouterr().out
    assert f"{native_blob}\0missing=yes\0path=line\nbreak.txt\0type=blob\0" in out
    assert 'path="line' not in out


def test_rev_list_z_allow_promisor_omits_missing_records(tmp_path, monkeypatch, capsys):
    repo, commit, native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "-z", "--missing=allow-promisor", "HEAD"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{commit}\0")
    assert native_blob not in out
    assert "missing=yes\0" not in out


def test_rev_list_z_rejects_objects_edge(tmp_path, monkeypatch):
    repo, _commit, _native_blob = _partial_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)

    with pytest.raises(ValueError, match="only compatible with --objects"):
        run_rev_list_disk_usage(["--objects-edge", "-z", "--missing=print-info", "HEAD"])
