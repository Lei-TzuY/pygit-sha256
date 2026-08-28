from __future__ import annotations

import hashlib
from types import SimpleNamespace

from pygit import clone_cli, clone_partial
from pygit.promisor import read_promisor_state
from pygit.remote import Advertisement, NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_commit(tree_oid: str) -> tuple[str, NativeObject]:
    data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\ntip"
    ).encode()
    oid = _native_oid("commit", data)
    return oid, NativeObject("commit", data, oid)


def _fake_cli_repo(tmp_path):
    return SimpleNamespace(
        refs=SimpleNamespace(current_branch=lambda: None),
        worktree=tmp_path / "clone",
    )


def test_clone_no_checkout_forwards_to_partial_path(tmp_path, monkeypatch):
    calls = []

    def fake_partial(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return _fake_cli_repo(tmp_path)

    monkeypatch.setattr(clone_cli, "clone_partial_repository", fake_partial)
    assert clone_cli.run_clone(
        [
            "-n",
            "--filter=blob:none",
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
        ]
    ) == 0
    assert calls[0][2]["checkout"] is False


def test_clone_no_checkout_forwards_to_shallow_path(tmp_path, monkeypatch):
    calls = []

    def fake_shallow(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return _fake_cli_repo(tmp_path)

    monkeypatch.setattr(clone_cli, "clone_shallow_repository", fake_shallow)
    assert clone_cli.run_clone(
        [
            "--no-checkout",
            "--depth=1",
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
        ]
    ) == 0
    assert calls[0][2]["checkout"] is False


def test_clone_default_call_shapes_do_not_gain_checkout_keyword(tmp_path, monkeypatch):
    calls = []

    def fake_partial(url, path, **kwargs):
        calls.append(kwargs)
        return _fake_cli_repo(tmp_path)

    monkeypatch.setattr(clone_cli, "clone_partial_repository", fake_partial)
    assert clone_cli.run_clone(
        [
            "--filter=blob:none",
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
        ]
    ) == 0
    assert "checkout" not in calls[0]


def test_repository_clone_checkout_suppression_restores_method():
    original = Repository._replace_worktree_from_commit
    with clone_cli._suppress_repository_clone_checkout(True):
        assert Repository._replace_worktree_from_commit is not original
    assert Repository._replace_worktree_from_commit is original


def test_partial_no_checkout_keeps_head_blob_promised_and_worktree_empty(
    tmp_path, monkeypatch
):
    blob_oid = "1" * 40
    tree_data = b"100644 hello.txt\x00" + bytes.fromhex(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_oid, commit_obj = _native_commit(tree_oid)
    tree_obj = NativeObject("tree", tree_data, tree_oid)

    advertisement = Advertisement(
        refs={"HEAD": commit_oid, "refs/heads/main": commit_oid},
        capabilities={"ls-refs", "fetch=filter"},
        symrefs={"HEAD": "refs/heads/main"},
    )

    class FakeClient:
        def __init__(self, url, timeout=30, *, server_options=()):
            self.url = url

        def discover_refs(self):
            return advertisement

    def fake_filtered(client, *, haves, advertisement, filter_spec):
        return SimpleNamespace(
            objects={commit_oid: commit_obj, tree_oid: tree_obj},
            shallow=(),
            unshallow=(),
        )

    def fail_materialize(*args, **kwargs):
        raise AssertionError("no-checkout must not materialize promised blobs")

    monkeypatch.setattr(clone_partial, "SmartHttpV2FetchClient", FakeClient)
    monkeypatch.setattr(clone_partial, "_filtered_v2_fetch", fake_filtered)
    monkeypatch.setattr(clone_partial, "materialize_promised_objects", fail_materialize)

    repo = clone_partial.clone_partial_repository(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        filter_spec="blob:none",
        branch_name=None,
        single_branch=False,
        checkout=False,
    )

    assert repo.refs.current_branch() == "main"
    assert not (repo.worktree / "hello.txt").exists()
    state = read_promisor_state(repo.pygit_dir)
    assert state["promised"][blob_oid] == "blob"
    assert blob_oid not in state["resolved"]
