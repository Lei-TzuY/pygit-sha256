from __future__ import annotations

import hashlib
from types import SimpleNamespace

from pygit import clone_partial
from pygit.config import GitConfig
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit import promisor_materialize as pm
from pygit.remote import Advertisement, NativeObject
from pygit.repo import Repository


OID = "33" * 20
LOCAL = "cc" * 32


class _FakeImporter:
    def __init__(self, store, objects, known=None):
        self.objects = objects

    def import_oid(self, oid):
        assert oid == OID
        return LOCAL


def _promisor_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    # Deliberately configure origin first. extensions.partialClone must still
    # move it behind cache-like promisors during missing-object lookup.
    repo.add_remote("origin", "https://origin.example/repo.git")
    repo.add_remote("cache", "https://cache.example/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={OID: "blob"},
    )
    return repo


def test_primary_promisor_is_tried_last_and_config_only_cache_is_discovered(
    tmp_path, monkeypatch
):
    repo = _promisor_repo(tmp_path)
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "cache.promisor", "true")
    calls = []

    def fake_one(url, oid, *, server_options=()):
        calls.append((url, oid, tuple(server_options)))
        if "cache.example" in url:
            return {}
        return {oid: object()}

    monkeypatch.setattr(pm, "_fetch_native_object", fake_one)
    monkeypatch.setattr(pm, "TagPreservingNativeImporter", _FakeImporter)

    result = pm.materialize_promised_objects(repo.pygit_dir, [OID])

    assert result == {OID: LOCAL}
    assert [call[0] for call in calls] == [
        "https://cache.example/repo.git",
        "https://origin.example/repo.git",
    ]
    state = read_promisor_state(repo.pygit_dir)
    assert OID not in state["promised"]
    assert state["resolved"][OID] == LOCAL


def test_partial_clone_filter_config_alone_marks_promisor_candidate(tmp_path):
    repo = _promisor_repo(tmp_path)
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "cache.partialCloneFilter", "blob:limit=1024")

    assert pm._ordered_promisor_remotes(repo, ("origin",)) == ("cache", "origin")


def test_primary_marker_adds_candidate_even_when_sidecar_did_not_record_it(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://origin.example/repo.git")
    repo.add_remote("cache", "https://cache.example/repo.git")
    repo.config_set("remote", "cache.promisor", "true")
    repo.config_set("extensions", "partialClone", "origin")

    assert pm._ordered_promisor_remotes(repo, ("cache",)) == ("cache", "origin")


def test_stale_primary_marker_does_not_block_working_cache(tmp_path, monkeypatch):
    repo = _promisor_repo(tmp_path)
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "cache.promisor", "true")
    repo.remove_remote("origin")
    calls = []

    def fake_one(url, oid, *, server_options=()):
        calls.append(url)
        return {oid: object()}

    monkeypatch.setattr(pm, "_fetch_native_object", fake_one)
    monkeypatch.setattr(pm, "TagPreservingNativeImporter", _FakeImporter)

    assert pm.materialize_promised_objects(repo.pygit_dir, [OID]) == {OID: LOCAL}
    assert calls == ["https://cache.example/repo.git"]


def test_without_primary_marker_phase221_configuration_order_is_preserved(tmp_path):
    repo = _promisor_repo(tmp_path)
    update_promisor_state(
        repo.pygit_dir,
        remote="cache",
        filter_spec="blob:none",
    )

    assert pm._ordered_promisor_remotes(repo, ("origin", "cache")) == (
        "origin",
        "cache",
    )


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_commit(tree_oid: str):
    data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\ntip"
    ).encode()
    oid = _native_oid("commit", data)
    return oid, NativeObject("commit", data, oid)


def test_partial_clone_persists_primary_promisor_marker(tmp_path, monkeypatch):
    tree_data = b""
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

    monkeypatch.setattr(clone_partial, "SmartHttpV2FetchClient", FakeClient)
    monkeypatch.setattr(clone_partial, "_filtered_v2_fetch", fake_filtered)

    repo = clone_partial.clone_partial_repository(
        "https://origin.example/repo.git",
        str(tmp_path / "clone"),
        filter_spec="blob:none",
        branch_name=None,
        single_branch=False,
        checkout=False,
    )

    config = GitConfig(repo.pygit_dir)
    assert config.get("extensions", "partialClone") == "origin"
    assert config.get("remote", "origin.promisor") == "true"
    assert config.get("remote", "origin.partialCloneFilter") == "blob:none"
