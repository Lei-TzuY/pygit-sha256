from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from pygit.objects import BlobObject, TreeObject
from pygit.objects.tree import TreeEntry
from pygit.promisor import PromisorMissingError, read_promisor_state, update_promisor_state
from pygit.promisor_materialize import (
    _fetch_native_object,
    _promisor_remote_for_many,
    materialize_promised_object,
)
from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.remote import NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def test_tree_entry_resolver_is_lazy_and_cached():
    native = "12" * 20
    local = "ab" * 32
    calls = []
    entry = TreeEntry("100644", "a.txt", native_oid=native)
    entry.set_resolver(lambda oid: calls.append(oid) or local)

    assert entry.is_resolved is False
    assert calls == []
    assert entry.sha == local
    assert calls == [native]
    assert entry.is_resolved is True
    assert entry.sha == local
    assert calls == [native]


def test_materialize_promised_blob_updates_state_and_sha256_store(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    data = b"promised payload\n"
    native = _native_oid("blob", data)
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={native: "blob"},
    )

    calls = []

    def fake_fetch(url, oid, *, server_options=()):
        calls.append((url, oid, tuple(server_options)))
        return {oid: NativeObject("blob", data, oid)}

    monkeypatch.setattr("pygit.promisor_materialize._fetch_native_object", fake_fetch)
    local = materialize_promised_object(repo.pygit_dir, native)

    assert len(local) == 64
    blob = repo.store.read(local)
    assert isinstance(blob, BlobObject)
    assert blob.data == data
    assert calls == [("https://example.test/repo.git", native, ())]
    state = read_promisor_state(repo.pygit_dir)
    assert native not in state["promised"]
    assert state["resolved"][native] == local

    # Resolved promises are a metadata-only fast path; no second network fetch.
    assert materialize_promised_object(repo.pygit_dir, native) == local
    assert len(calls) == 1


def test_native_tree_materializes_only_when_entry_sha_is_consumed(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    data = b"lazy checkout payload\n"
    native = _native_oid("blob", data)
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={native: "blob"},
    )
    tree_sha = repo.store.write(
        TreeObject(
            [TreeEntry("100644", "a.txt", native_oid=native)],
            native_entries=True,
        )
    )

    calls = []
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda url, oid, *, server_options=(): calls.append(oid)
        or {oid: NativeObject("blob", data, oid)},
    )

    tree = repo.store.read(tree_sha)
    assert calls == []
    assert tree.entries[0].is_resolved is False

    local = tree.entries[0].sha
    assert calls == [native]
    assert repo.store.read(local).data == data
    # The runtime resolution never changes the canonical foreign-tree identity.
    assert repo.store.write(tree) == tree_sha

    again = repo.store.read(tree_sha)
    assert again.entries[0].sha == local
    assert calls == [native]


def test_materialization_refuses_unpromised_oid(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    with pytest.raises(PromisorMissingError):
        materialize_promised_object(repo.pygit_dir, "34" * 20)


def test_legacy_single_owner_helper_requires_unambiguous_promisor_remote(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "56" * 20
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={native: "blob"},
    )
    update_promisor_state(
        repo.pygit_dir,
        remote="backup",
        filter_spec="blob:none",
    )
    # Phase221 intentionally makes the public lazy materializer multi-promisor.
    # Keep the historical Phase213 ambiguity contract on the compatibility helper
    # rather than forcing the public operation back to single-owner semantics.
    with pytest.raises(RuntimeError, match="exactly one promisor remote"):
        _promisor_remote_for_many(repo.pygit_dir, [native])


def test_fetch_native_object_requests_exact_promised_oid(monkeypatch):
    native = "78" * 20
    seen = {}
    capabilities = ProtocolV2Capabilities(
        {
            "ls-refs": "unborn",
            "fetch": "",
            "object-format": "sha1",
            "server-option": None,
        }
    )

    class FakeClient:
        def __init__(self, url, *, server_options=()):
            seen["url"] = url
            seen["options"] = tuple(server_options)

        def discover_capabilities(self):
            return capabilities

        def _post_fetch(self, body):
            seen["body"] = body
            return SimpleNamespace(shallow=(), unshallow=(), pack=b"PACK-fake")

    class FakeParser:
        def __init__(self, pack):
            assert pack == b"PACK-fake"

        def parse(self):
            return {native: NativeObject("blob", b"x", native)}

    monkeypatch.setattr("pygit.promisor_materialize.SmartHttpV2FetchClient", FakeClient)
    monkeypatch.setattr("pygit.promisor_materialize.PackParser", FakeParser)

    objects = _fetch_native_object(
        "https://example.test/repo.git",
        native,
        server_options=("trace=1",),
    )
    assert native in objects
    assert seen["url"] == "https://example.test/repo.git"
    assert seen["options"] == ("trace=1",)
    assert f"want {native}\n".encode() in seen["body"]
    assert b"filter " not in seen["body"]
