from __future__ import annotations

import pytest

from pygit import Repository
from pygit.promisor import PromisorMissingError, read_promisor_state, update_promisor_state
from pygit import promisor_materialize as pm


OID_A = "11" * 20
OID_B = "22" * 20
SHA_A = "aa" * 32
SHA_B = "bb" * 32


class _FakeImporter:
    def __init__(self, store, objects, known=None):
        self.objects = objects

    def import_oid(self, oid):
        return {OID_A: SHA_A, OID_B: SHA_B}[oid]


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("cache", "https://cache.example/repo.git")
    repo.add_remote("origin", "https://origin.example/repo.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="cache",
        filter_spec="blob:none",
        promised={OID_A: "blob", OID_B: "blob"},
    )
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={OID_A: "blob", OID_B: "blob"},
    )
    return repo


def test_multi_promisor_shrinks_batch_across_remotes(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_fetch(url, native_oids, *, server_options=()):
        calls.append((url, tuple(native_oids), tuple(server_options)))
        if "cache.example" in url:
            return {OID_A: object()}
        return {OID_B: object()}

    monkeypatch.setattr(pm, "_fetch_native_objects", fake_fetch)
    monkeypatch.setattr(pm, "TagPreservingNativeImporter", _FakeImporter)
    monkeypatch.setattr(
        pm,
        "configured_server_options",
        lambda _repo, remote: [f"remote={remote}"],
    )

    resolved = pm.materialize_promised_objects(repo.pygit_dir, [OID_A, OID_B])

    assert resolved == {OID_A: SHA_A, OID_B: SHA_B}
    assert calls == [
        (
            "https://cache.example/repo.git",
            (OID_A, OID_B),
            ("remote=cache",),
        ),
        (
            "https://origin.example/repo.git",
            (OID_B,),
            ("remote=origin",),
        ),
    ]
    state = read_promisor_state(repo.pygit_dir)
    assert OID_A not in state["promised"]
    assert OID_B not in state["promised"]
    assert state["resolved"][OID_A] == SHA_A
    assert state["resolved"][OID_B] == SHA_B


def test_multi_promisor_falls_through_transport_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_fetch(url, native_oids, *, server_options=()):
        calls.append(url)
        if "cache.example" in url:
            raise RuntimeError("cache cannot satisfy want")
        return {oid: object() for oid in native_oids}

    monkeypatch.setattr(pm, "_fetch_native_objects", fake_fetch)
    monkeypatch.setattr(pm, "TagPreservingNativeImporter", _FakeImporter)

    resolved = pm.materialize_promised_objects(repo.pygit_dir, [OID_A, OID_B])

    assert resolved == {OID_A: SHA_A, OID_B: SHA_B}
    assert calls == [
        "https://cache.example/repo.git",
        "https://origin.example/repo.git",
    ]


def test_multi_promisor_missing_everywhere_preserves_promisor_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    monkeypatch.setattr(pm, "_fetch_native_objects", lambda *args, **kwargs: {})

    with pytest.raises(PromisorMissingError) as excinfo:
        pm.materialize_promised_objects(repo.pygit_dir, [OID_A, OID_B])

    assert excinfo.value.native_oid == OID_A
    state = read_promisor_state(repo.pygit_dir)
    assert OID_A in state["promised"]
    assert OID_B in state["promised"]


def test_single_remote_compatibility_helper_still_rejects_ambiguous_owner(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(RuntimeError, match="exactly one promisor remote"):
        pm._promisor_remote_for_many(repo.pygit_dir, [OID_A])


def test_unconfigured_recorded_remote_is_skipped_before_working_fallback(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.remove_remote("cache")
    calls = []

    def fake_fetch(url, native_oids, *, server_options=()):
        calls.append(url)
        return {oid: object() for oid in native_oids}

    monkeypatch.setattr(pm, "_fetch_native_objects", fake_fetch)
    monkeypatch.setattr(pm, "TagPreservingNativeImporter", _FakeImporter)

    resolved = pm.materialize_promised_objects(repo.pygit_dir, [OID_A, OID_B])

    assert resolved == {OID_A: SHA_A, OID_B: SHA_B}
    assert calls == ["https://origin.example/repo.git"]
