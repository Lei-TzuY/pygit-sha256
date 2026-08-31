from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_remote_fetch as phase329
from pygit.protocol_v2_packfile_uri_remote_fetch import (
    NamedRemotePackfileUriFetchResult,
    fetch_named_remote_with_packfile_uris,
)
from pygit.refs import ZERO_SHA
from pygit.remote import Advertisement
from pygit.repo import Repository


OID_MAIN = "1" * 40
OID_FEATURE = "2" * 40
LOCAL_OLD = "a" * 64


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/acme/repo.git")
    return repo


def _advertisement() -> Advertisement:
    return Advertisement(
        refs={
            "HEAD": OID_MAIN,
            "refs/heads/main": OID_MAIN,
            "refs/heads/feature": OID_FEATURE,
            "refs/tags/v1": "3" * 40,
        },
        capabilities={"version 2"},
        symrefs={"HEAD": "refs/heads/main"},
    )


class _FakeClient:
    advertisement = _advertisement()
    init_args = None

    def __init__(self, url, timeout=30, *, server_options=()):
        type(self).init_args = (url, timeout, tuple(server_options))
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.server_options = tuple(server_options)

    def discover_refs(self):
        return type(self).advertisement


def test_named_remote_composes_discovery_planning_and_repository_fetch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []
    repository_sentinel = object()

    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", _FakeClient)

    def fake_fetch(repo_arg, client, protocols, expected_roots, publications, **kwargs):
        calls.append((repo_arg, client, tuple(protocols), expected_roots, publications, kwargs))
        return repository_sentinel

    monkeypatch.setattr(phase329, "fetch_packfile_uris_into_repository", fake_fetch)

    result = fetch_named_remote_with_packfile_uris(
        repo,
        protocols=("https", "http"),
        timeout=17,
        server_options=("trace2=off",),
    )

    assert isinstance(result, NamedRemotePackfileUriFetchResult)
    assert result.remote == "origin"
    assert result.url == "https://example.invalid/acme/repo.git"
    assert result.advertisement is _FakeClient.advertisement
    assert result.repository is repository_sentinel
    assert result.plan.expected_roots == {OID_MAIN: b"commit", OID_FEATURE: b"commit"}
    assert set(result.plan.publications) == {
        "refs/remotes/origin/main",
        "refs/remotes/origin/feature",
    }
    assert result.plan.default_branch == "main"

    assert _FakeClient.init_args == (
        "https://example.invalid/acme/repo.git",
        17,
        ("trace2=off",),
    )
    assert len(calls) == 1
    _, client, protocols, roots, publications, kwargs = calls[0]
    assert isinstance(client, _FakeClient)
    assert protocols == ("https", "http")
    assert roots == result.plan.expected_roots
    assert publications == result.plan.publications
    assert kwargs["advertisement"] is _FakeClient.advertisement
    assert kwargs["message"] == "fetch: origin via verified protocol-v2 packfile-uri"


def test_existing_tracking_sha256_is_used_as_exact_named_remote_cas(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.refs.set_remote("origin", "main", LOCAL_OLD)
    captured = {}

    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", _FakeClient)

    def fake_fetch(repo_arg, client, protocols, expected_roots, publications, **kwargs):
        captured.update(publications)
        return object()

    monkeypatch.setattr(phase329, "fetch_packfile_uris_into_repository", fake_fetch)

    result = fetch_named_remote_with_packfile_uris(
        repo,
        branches=["refs/heads/main"],
    )

    publication = captured["refs/remotes/origin/main"]
    assert publication.old_local_oid == LOCAL_OLD
    assert publication.native_oid == OID_MAIN
    assert result.plan.expected_roots == {OID_MAIN: b"commit"}
    assert "refs/remotes/origin/feature" not in result.plan.publications


def test_missing_tracking_ref_uses_local_zero_sha_creation_cas(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    captured = {}

    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", _FakeClient)

    def fake_fetch(repo_arg, client, protocols, expected_roots, publications, **kwargs):
        captured.update(publications)
        return object()

    monkeypatch.setattr(phase329, "fetch_packfile_uris_into_repository", fake_fetch)

    fetch_named_remote_with_packfile_uris(
        repo,
        branches=["refs/heads/main"],
    )

    assert captured["refs/remotes/origin/main"].old_local_oid == ZERO_SHA
    assert len(ZERO_SHA) == 64


def test_v0_initial_fallback_returns_none_before_planning_or_publication(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    class V0Client(_FakeClient):
        def discover_refs(self):
            return None

    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", V0Client)

    def forbidden(*args, **kwargs):
        raise AssertionError("repository transaction must not run for v0 fallback")

    monkeypatch.setattr(phase329, "fetch_packfile_uris_into_repository", forbidden)

    assert fetch_named_remote_with_packfile_uris(repo) is None
    assert repo.refs.list_remotes("origin") == []


def test_protocol_downgrade_after_v2_discovery_fails_closed(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", _FakeClient)
    monkeypatch.setattr(
        phase329,
        "fetch_packfile_uris_into_repository",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(RuntimeError, match="stopped speaking protocol v2"):
        fetch_named_remote_with_packfile_uris(repo)

    assert repo.refs.list_remotes("origin") == []


def test_unknown_or_invalid_remote_config_fails_before_network(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("client must not be constructed")

    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", ForbiddenClient)

    with pytest.raises(KeyError, match="Unknown remote"):
        fetch_named_remote_with_packfile_uris(repo, "missing")

    config = repo._read_config()
    config["remotes"]["broken"] = {"url": ""}
    repo._write_config(config)
    with pytest.raises(ValueError, match="valid URL"):
        fetch_named_remote_with_packfile_uris(repo, "broken")


def test_transport_and_external_resource_options_are_forwarded(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    captured = {}
    opener = object()

    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", _FakeClient)

    def fake_fetch(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(phase329, "fetch_packfile_uris_into_repository", fake_fetch)

    fetch_named_remote_with_packfile_uris(
        repo,
        haves=["9" * 40],
        shallow=["8" * 40],
        deepen=4,
        deepen_relative=True,
        message="custom fetch",
        external_timeout=23,
        max_pack_bytes=101,
        max_total_bytes=202,
        max_packs=3,
        opener=opener,
    )

    assert captured["haves"] == ["9" * 40]
    assert captured["shallow"] == ["8" * 40]
    assert captured["deepen"] == 4
    assert captured["deepen_relative"] is True
    assert captured["message"] == "custom fetch"
    assert captured["external_timeout"] == 23
    assert captured["max_pack_bytes"] == 101
    assert captured["max_total_bytes"] == 202
    assert captured["max_packs"] == 3
    assert captured["opener"] is opener


def test_named_remote_planning_rejects_unadvertised_branch_before_repository_fetch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(phase329, "SmartHttpV2PackfileUriClient", _FakeClient)

    def forbidden(*args, **kwargs):
        raise AssertionError("repository transaction must not run")

    monkeypatch.setattr(phase329, "fetch_packfile_uris_into_repository", forbidden)

    with pytest.raises(ValueError, match="not advertised"):
        fetch_named_remote_with_packfile_uris(
            repo,
            branches=["refs/heads/missing"],
        )
