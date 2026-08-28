from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from pygit import clone_cli, clone_partial
from pygit.objects import BlobObject
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit.promisor_materialize import materialize_promised_objects
from pygit.remote import Advertisement, NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_commit(tree_oid: str, message: str = "tip") -> tuple[str, NativeObject]:
    data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        f"\n{message}"
    ).encode()
    oid = _native_oid("commit", data)
    return oid, NativeObject("commit", data, oid)


def test_clone_filter_validator_matches_partial_fetch_grammar():
    assert clone_cli._filter_spec("blob:none") == "blob:none"
    assert clone_cli._filter_spec("blob:limit=4096") == "blob:limit=4096"
    with pytest.raises(Exception, match="supports only"):
        clone_cli._filter_spec("tree:0")


def test_clone_rejects_filter_plus_depth_before_transport(tmp_path):
    with pytest.raises(SystemExit):
        clone_cli.run_clone(
            [
                "--filter=blob:none",
                "--depth=1",
                "https://example.test/repo.git",
                str(tmp_path / "clone"),
            ]
        )


def test_clone_cli_forwards_filter_and_ordered_server_options(tmp_path, monkeypatch):
    calls = []
    result = SimpleNamespace(
        refs=SimpleNamespace(current_branch=lambda: None),
        worktree=tmp_path / "clone",
    )

    def fake_partial(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return result

    monkeypatch.setattr(clone_cli, "clone_partial_repository", fake_partial)
    assert clone_cli.run_clone(
        [
            "--filter=blob:none",
            "--server-option=one",
            "--server-option",
            "two",
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
        ]
    ) == 0
    assert calls == [
        (
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
            {
                "filter_spec": "blob:none",
                "branch_name": None,
                "single_branch": False,
                "server_options": ("one", "two"),
            },
        )
    ]


def test_batch_materialization_uses_one_fetch_and_caches(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    oid_a = "a" * 40
    oid_b = "b" * 40
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={oid_a: "blob", oid_b: "blob"},
    )
    calls = []

    def fake_fetch(url, native_oids, *, server_options=()):
        calls.append((url, tuple(native_oids), tuple(server_options)))
        return {
            oid_a: NativeObject("blob", b"alpha", oid_a),
            oid_b: NativeObject("blob", b"beta", oid_b),
        }

    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        fake_fetch,
    )
    first = materialize_promised_objects(repo.pygit_dir, [oid_a, oid_b, oid_a])
    assert calls == [
        ("https://example.test/repo.git", (oid_a, oid_b), ())
    ]
    assert repo.store.read(first[oid_a]).data == b"alpha"
    assert repo.store.read(first[oid_b]).data == b"beta"
    state = read_promisor_state(repo.pygit_dir)
    assert oid_a not in state["promised"]
    assert oid_b not in state["promised"]
    assert state["resolved"][oid_a] == first[oid_a]
    assert state["resolved"][oid_b] == first[oid_b]

    second = materialize_promised_objects(repo.pygit_dir, [oid_b, oid_a])
    assert second[oid_a] == first[oid_a]
    assert second[oid_b] == first[oid_b]
    assert len(calls) == 1


def test_partial_clone_batches_head_blobs_and_keeps_foreign_tree_stable(
    tmp_path, monkeypatch
):
    blob_oid = "1" * 40
    tree_data = b"100644 hello.txt\x00" + bytes.fromhex(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_oid, commit_obj = _native_commit(tree_oid)
    tree_obj = NativeObject("tree", tree_data, tree_oid)
    dev_oid, dev_obj = _native_commit(tree_oid, "dev")

    advertisement = Advertisement(
        refs={
            "HEAD": commit_oid,
            "refs/heads/main": commit_oid,
            "refs/heads/dev": dev_oid,
        },
        capabilities={"ls-refs", "fetch=filter", "server-option"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    events = []

    class FakeClient:
        def __init__(self, url, timeout=30, *, server_options=()):
            self.url = url
            self.timeout = timeout
            self.server_options = tuple(server_options)
            events.append(("client", self.server_options))

        def discover_refs(self):
            events.append(("discover", self.server_options))
            return advertisement

    def fake_filtered(client, *, haves, advertisement, filter_spec):
        events.append(
            (
                "filtered",
                filter_spec,
                tuple(sorted(advertisement.refs)),
                client.server_options,
            )
        )
        return SimpleNamespace(
            objects={
                commit_oid: commit_obj,
                dev_oid: dev_obj,
                tree_oid: tree_obj,
            },
            shallow=(),
            unshallow=(),
        )

    materialized = []

    def fake_materialize(pygit_dir, native_oids):
        native_oids = tuple(sorted(native_oids))
        materialized.append(native_oids)
        repo = Repository(str(pygit_dir.parent))
        local_blob = repo.store.write(BlobObject(b"hello from promisor\n"))
        update_promisor_state(
            pygit_dir,
            resolved={blob_oid: local_blob},
        )
        return {blob_oid: local_blob}

    monkeypatch.setattr(clone_partial, "SmartHttpV2FetchClient", FakeClient)
    monkeypatch.setattr(clone_partial, "_filtered_v2_fetch", fake_filtered)
    monkeypatch.setattr(
        clone_partial,
        "materialize_promised_objects",
        fake_materialize,
    )

    repo = clone_partial.clone_partial_repository(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        filter_spec="blob:none",
        branch_name=None,
        single_branch=False,
        server_options=("trace=one", "trace=two"),
    )

    assert events[0] == ("client", ("trace=one", "trace=two"))
    assert events[1] == ("discover", ("trace=one", "trace=two"))
    assert events[2][0:2] == ("filtered", "blob:none")
    assert set(events[2][2]) == {"refs/heads/main", "refs/heads/dev"}
    assert materialized == [(blob_oid,)]
    assert (repo.worktree / "hello.txt").read_bytes() == b"hello from promisor\n"
    assert repo.refs.get_remote("origin", "main") is not None
    assert repo.refs.get_remote("origin", "dev") is not None
    assert repo.refs.current_branch() == "main"
    assert repo.config_get("protocol", "version") == "2"
    assert repo.config_get("remote", "origin.promisor") == "true"
    assert repo.config_get("remote", "origin.partialCloneFilter") == "blob:none"

    commit = repo.store.read(repo.refs.resolve_head())
    tree_sha = commit.tree
    before = repo.store.read(tree_sha).serialize()
    assert repo.store.write(repo.store.read(tree_sha)) == tree_sha
    after = repo.store.read(tree_sha).serialize()
    assert after == before


def test_partial_clone_single_branch_selects_only_target(tmp_path, monkeypatch):
    main_oid = "a" * 40
    dev_oid = "b" * 40
    advertisement = Advertisement(
        refs={
            "HEAD": main_oid,
            "refs/heads/main": main_oid,
            "refs/heads/dev": dev_oid,
        },
        capabilities={"fetch=filter"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    selected = clone_partial._selected_branch_refs(
        advertisement,
        target_branch="dev",
        single_branch=True,
    )
    assert selected == {"refs/heads/dev": dev_oid}
