from __future__ import annotations

from contextlib import contextmanager

import pytest

from pygit.fetch_cli_dry_run import run_fetch
from pygit.fetch_protocol_v2 import (
    negotiate_only,
    protocol_v2_requested,
    protocol_v2_transport,
)
from pygit.protocol_v2 import ProtocolV2Capabilities, SmartHttpV2QueryClient
from pygit.protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    SmartHttpV2FetchClient,
    build_fetch_request,
)
from pygit.remote import Advertisement, FetchResult, SmartHttpClient, pkt_line
from pygit.repo import Repository


def _caps(fetch_value: str = "wait-for-done") -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "ls-refs": "unborn",
            "fetch": fetch_value,
            "object-format": "sha1",
        }
    )


def _commit(repo: Repository, text: str = "one") -> str:
    path = repo.worktree / "a.txt"
    path.write_text(text, encoding="utf-8")
    repo.add(["a.txt"])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def test_wait_for_done_fetch_request_omits_done():
    body = build_fetch_request(
        _caps(),
        ["a" * 40],
        haves=["b" * 40],
        done=False,
        wait_for_done=True,
    )

    assert pkt_line(b"wait-for-done\n") in body
    assert pkt_line(b"done\n") not in body
    assert body.endswith(b"0000")


def test_wait_for_done_requires_server_feature():
    with pytest.raises(RuntimeError, match="wait-for-done"):
        build_fetch_request(
            _caps("shallow"),
            ["a" * 40],
            done=False,
            wait_for_done=True,
        )


def test_v2_client_negotiate_returns_ack_without_pack(monkeypatch):
    common = "b" * 40
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    client = SmartHttpV2FetchClient("https://example.test/repo.git")

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(
        client,
        "_post_fetch",
        lambda body: ProtocolV2FetchResponse(
            acknowledgments=(common,),
            ready=False,
            nak=False,
            shallow=(),
            unshallow=(),
            wanted_refs={},
            pack=None,
        ),
    )

    assert client.negotiate(haves=[common], advertisement=advertisement) == (common,)


def test_v2_client_negotiate_rejects_pack_transition(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(
        client,
        "_post_fetch",
        lambda body: ProtocolV2FetchResponse(
            acknowledgments=(),
            ready=True,
            nak=False,
            shallow=(),
            unshallow=(),
            wanted_refs={},
            pack=b"PACK",
        ),
    )

    with pytest.raises(ValueError, match="wait-for-done"):
        client.negotiate(haves=["b" * 40], advertisement=advertisement)


def test_protocol_preference_probe_is_transparent_for_wrapper_standins():
    assert protocol_v2_requested(None) is False
    assert protocol_v2_requested(object()) is False


def test_protocol_v2_transport_routes_discovery(monkeypatch):
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    calls = []

    def fake_refs(self, *args, **kwargs):
        calls.append(self.url)
        return expected

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_refs", fake_refs)

    with protocol_v2_transport():
        actual = SmartHttpClient("https://example.test/repo.git").discover()

    assert actual is expected
    assert calls == ["https://example.test/repo.git"]


def test_protocol_v2_transport_falls_back_to_v0(monkeypatch):
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    calls = []

    def fake_v0(self):
        calls.append(self.url)
        return expected

    monkeypatch.setattr(SmartHttpClient, "discover", fake_v0)
    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_refs", lambda self: None)

    with protocol_v2_transport():
        actual = SmartHttpClient("https://example.test/repo.git").discover()

    assert actual is expected
    assert calls == ["https://example.test/repo.git"]


def test_protocol_v2_transport_routes_fetch(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    expected = FetchResult(advertisement, {})
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return expected

    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", fake_fetch)

    with protocol_v2_transport():
        actual = SmartHttpClient("https://example.test/repo.git").fetch(
            haves={"b" * 40},
            advertisement=advertisement,
        )

    assert actual is expected
    assert calls == [{"b" * 40}]


def test_negotiate_only_maps_native_ack_to_sha256(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    repo.add_remote("origin", "https://example.test/repo.git")
    seen = {}

    monkeypatch.setattr(
        SmartHttpV2FetchClient,
        "discover_refs",
        lambda self: Advertisement({"refs/heads/main": "f" * 40}, set(), {}),
    )

    def fake_negotiate(self, *, haves, advertisement=None):
        seen["haves"] = set(haves)
        return (next(iter(seen["haves"])),)

    monkeypatch.setattr(SmartHttpV2FetchClient, "negotiate", fake_negotiate)

    assert negotiate_only(repo, source="origin", restrict=["main"]) == [tip]
    assert all(len(oid) == 40 for oid in seen["haves"])


def test_negotiate_only_requires_protocol_v2(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.add_remote("origin", "https://example.test/repo.git")
    monkeypatch.setattr(SmartHttpV2FetchClient, "discover_refs", lambda self: None)

    with pytest.raises(RuntimeError, match="requires protocol version 2"):
        negotiate_only(repo, source="origin", restrict=["main"])


def test_run_fetch_negotiate_only_prints_sha256(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    repo.add_remote("origin", "https://example.test/repo.git")
    capsys.readouterr()

    def fake_negotiate(repo_arg, *, source, restrict, include=()):
        assert repo_arg.worktree == repo.worktree
        assert source == "origin"
        assert restrict == ["main"]
        return [tip]

    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiate_only", fake_negotiate)
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(["--negotiate-only", "--negotiation-tip=main", "origin"]) == 0
    assert capsys.readouterr().out.strip() == tip


def test_protocol_version_two_composes_with_existing_fetch(monkeypatch, tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.config_set("protocol", "version", "2")
    events = []

    @contextmanager
    def v2_scope():
        events.append("v2-enter")
        yield
        events.append("v2-exit")

    monkeypatch.setattr("pygit.fetch_cli_dry_run.protocol_v2_transport", v2_scope)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: events.append(tuple(argv)) or 0,
    )
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(["origin"]) == 0
    assert events == ["v2-enter", ("origin",), "v2-exit"]


def test_refetch_dry_run_standin_remains_compatible(monkeypatch):
    events = []

    @contextmanager
    def refetch_scope():
        events.append("refetch-enter")
        yield
        events.append("refetch-exit")

    @contextmanager
    def dry_scope(repo):
        events.append("dry-enter")
        yield
        events.append("dry-exit")

    monkeypatch.setattr("pygit.fetch_cli_dry_run.refetch_transport", refetch_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.dry_run_repository", dry_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: object())
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: events.append(tuple(argv)) or 0,
    )

    assert run_fetch(["--dry-run", "--refetch", "origin"]) == 0
    assert events == [
        "refetch-enter",
        "dry-enter",
        ("origin", "--no-write-fetch-head"),
        "dry-exit",
        "refetch-exit",
    ]
