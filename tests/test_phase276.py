from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import pygit.fetch_partial as fetch_partial
from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.promisor import (
    promised_size,
    read_promisor_state,
    update_promisor_state,
)
from pygit.remote import Advertisement, NativeObject
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _filtered_fixture(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")

    blob_data = b"promised payload\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = b"100644 f.txt\0" + bytes.fromhex(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_data = (
        f"tree {tree_oid}\n"
        "author Test <test@example.com> 1 +0000\n"
        "committer Test <test@example.com> 1 +0000\n"
        "\nmsg\n"
    ).encode()
    commit_oid = _native_oid("commit", commit_data)
    objects = {
        tree_oid: NativeObject("tree", tree_data, tree_oid),
        commit_oid: NativeObject("commit", commit_data, commit_oid),
    }
    return repo, blob_oid, commit_oid, objects, len(blob_data)


def test_legacy_version_one_promisor_state_reads_with_empty_sizes(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "a" * 40
    legacy = {
        "version": 1,
        "remotes": {"origin": {"filter": "blob:none"}},
        "promised": {native: "blob"},
        "resolved": {},
    }
    (repo.pygit_dir / "promisor.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    state = read_promisor_state(repo.pygit_dir)
    assert state["version"] == 1
    assert state["sizes"] == {}
    assert promised_size(repo.pygit_dir, native) is None


def test_promisor_size_records_only_for_unresolved_promises(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    promised = "a" * 40
    unrelated = "b" * 40
    update_promisor_state(
        repo.pygit_dir,
        promised={promised: "blob"},
        sizes={promised: 123, unrelated: 456},
    )

    state = read_promisor_state(repo.pygit_dir)
    assert state["sizes"] == {promised: 123}
    assert promised_size(repo.pygit_dir, promised) == 123
    assert promised_size(repo.pygit_dir, unrelated) is None


def test_resolving_promise_removes_stale_size_metadata(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "a" * 40
    local = "b" * 64
    update_promisor_state(
        repo.pygit_dir,
        promised={native: "blob"},
        sizes={native: 7},
    )
    assert promised_size(repo.pygit_dir, native) == 7

    update_promisor_state(repo.pygit_dir, resolved={native: local})
    state = read_promisor_state(repo.pygit_dir)
    assert native not in state["promised"]
    assert native not in state["sizes"]
    assert state["resolved"][native] == local
    assert promised_size(repo.pygit_dir, native) is None


@pytest.mark.parametrize("bad_size", [-1, True, 1.5, "7"])
def test_promisor_size_rejects_untrusted_invalid_values(tmp_path, bad_size):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "a" * 40
    update_promisor_state(repo.pygit_dir, promised={native: "blob"})

    with pytest.raises(ValueError, match="non-negative integer"):
        update_promisor_state(repo.pygit_dir, sizes={native: bad_size})

    assert promised_size(repo.pygit_dir, native) is None


def test_filtered_importer_exposes_only_native_promises(tmp_path):
    repo, blob_oid, commit_oid, objects, _size = _filtered_fixture(tmp_path)
    importer = PromisorFilteredNativeImporter(
        repo.store,
        objects,
        remote="origin",
        filter_spec="blob:none",
    )

    importer.import_oid(commit_oid)
    assert importer.promised_native_oids == (blob_oid,)
    assert promised_size(repo.pygit_dir, blob_oid) is None


def test_filtered_import_enriches_size_without_content_materialization(
    tmp_path, monkeypatch
):
    repo, blob_oid, commit_oid, objects, expected_size = _filtered_fixture(tmp_path)
    seen = {}

    class FakeInfoClient:
        def __init__(self, url, *, timeout=30, server_options=()):
            seen["url"] = url
            seen["timeout"] = timeout
            seen["options"] = tuple(server_options)

        def query_sizes(self, oids):
            seen["oids"] = tuple(oids)
            return {blob_oid: expected_size}

    monkeypatch.setattr(
        fetch_partial,
        "SmartHttpV2ObjectInfoClient",
        FakeInfoClient,
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_object",
        lambda *args, **kwargs: pytest.fail("size enrichment must not single-fetch content"),
    )
    monkeypatch.setattr(
        "pygit.promisor_materialize._fetch_native_objects",
        lambda *args, **kwargs: pytest.fail("size enrichment must not batch-fetch content"),
    )

    class FakeClient:
        url = "https://example.test/repo.git"
        timeout = 17

        def fetch(self, haves=None, advertisement=None):
            return SimpleNamespace(objects=objects)

    advertisement = Advertisement(refs={"refs/heads/main": commit_oid})
    native_map = {}
    known_by_native = {}
    previous = fetch_partial._ACTIVE_FILTER
    fetch_partial._ACTIVE_FILTER = (
        "origin",
        "blob:none",
        ("trace=one", "trace=two"),
    )
    try:
        imported, count = fetch_partial._fetch_import_sources_filtered(
            repo,
            FakeClient(),
            advertisement,
            {"refs/heads/main": commit_oid},
            native_map,
            known_by_native,
        )
    finally:
        fetch_partial._ACTIVE_FILTER = previous

    assert len(imported["refs/heads/main"]) == 64
    assert count == len(objects)
    assert seen == {
        "url": "https://example.test/repo.git",
        "timeout": 17,
        "options": ("trace=one", "trace=two"),
        "oids": (blob_oid,),
    }
    assert promised_size(repo.pygit_dir, blob_oid) == expected_size


def test_optional_object_info_failure_does_not_fail_filtered_fetch(tmp_path, monkeypatch):
    repo, blob_oid, commit_oid, objects, _expected_size = _filtered_fixture(tmp_path)

    class UnsupportedInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            raise RuntimeError("Remote protocol-v2 server does not advertise object-info")

    monkeypatch.setattr(
        fetch_partial,
        "SmartHttpV2ObjectInfoClient",
        UnsupportedInfoClient,
    )

    class FakeClient:
        url = "https://example.test/repo.git"
        timeout = 30

        def fetch(self, haves=None, advertisement=None):
            return SimpleNamespace(objects=objects)

    previous = fetch_partial._ACTIVE_FILTER
    fetch_partial._ACTIVE_FILTER = ("origin", "blob:none", ())
    try:
        imported, count = fetch_partial._fetch_import_sources_filtered(
            repo,
            FakeClient(),
            Advertisement(refs={"refs/heads/main": commit_oid}),
            {"refs/heads/main": commit_oid},
            {},
            {},
        )
    finally:
        fetch_partial._ACTIVE_FILTER = previous

    assert len(imported["refs/heads/main"]) == 64
    assert count == len(objects)
    assert blob_oid in read_promisor_state(repo.pygit_dir)["promised"]
    assert promised_size(repo.pygit_dir, blob_oid) is None


def test_unknown_object_info_result_is_not_persisted(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "a" * 40
    update_promisor_state(repo.pygit_dir, promised={native: "blob"})

    class UnknownInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            return {native: None}

    monkeypatch.setattr(
        fetch_partial,
        "SmartHttpV2ObjectInfoClient",
        UnknownInfoClient,
    )
    client = SimpleNamespace(url="https://example.test/repo.git", timeout=30)
    fetch_partial._record_promisor_sizes(repo, client, [native])

    assert promised_size(repo.pygit_dir, native) is None
    assert read_promisor_state(repo.pygit_dir)["sizes"] == {}
