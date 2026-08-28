from __future__ import annotations

import hashlib

import pytest

from pygit.fetch_cli_dry_run import run_fetch
from pygit.fetch_protocol_v2 import negotiate_only, protocol_v2_transport
from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    ProtocolV2Unavailable,
    SmartHttpV2FetchClient,
    build_fetch_request,
    parse_fetch_response,
)
from pygit.remote import (
    Advertisement,
    NativeObject,
    SmartHttpClient,
    build_pack,
    pkt_line,
)
from pygit.repo import Repository


def _caps(value: str = "wait-for-done") -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "agent": "git/test",
            "ls-refs": "unborn",
            "fetch": value,
            "object-format": "sha1",
        }
    )


def _blob_native(data: bytes = b"hello\n") -> NativeObject:
    oid = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    return NativeObject("blob", data, oid)


def _commit(repo: Repository, text: str = "one") -> str:
    path = repo.worktree / "a.txt"
    path.write_text(text, encoding="utf-8")
    repo.add(["a.txt"])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def test_build_fetch_request_uses_command_delimiter_and_wait_for_done():
    want = "a" * 40
    have = "b" * 40

    body = build_fetch_request(
        _caps(),
        wants=[want],
        haves=[have],
        done=False,
        wait_for_done=True,
    )

    assert body.startswith(pkt_line(b"command=fetch\n"))
    assert pkt_line(b"agent=pygit/0.1\n") in body
    assert b"0001" in body
    assert pkt_line(b"wait-for-done\n") in body
    assert pkt_line(f"want {want}\n".encode()) in body
    assert pkt_line(f"have {have}\n".encode()) in body
    assert pkt_line(b"done\n") not in body
    assert body.endswith(b"0000")


def test_build_fetch_request_requires_wait_for_done_feature():
    with pytest.raises(RuntimeError, match="wait-for-done"):
        build_fetch_request(
            _caps("shallow"),
            wants=["a" * 40],
            haves=["b" * 40],
            done=False,
            wait_for_done=True,
        )


def test_parse_acknowledgments_only_response():
    common = "a" * 40
    data = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {common}\n".encode())
        + pkt_line(b"ready\n")
        + b"0000"
    )

    parsed = parse_fetch_response(data)

    assert parsed.acknowledgments == (common,)
    assert parsed.ready is True
    assert parsed.pack is None


def test_parse_sectioned_packfile_sideband():
    blob = _blob_native()
    pack = build_pack([blob])
    midpoint = len(pack) // 2
    data = (
        pkt_line(b"shallow-info\n")
        + pkt_line(f"shallow {'c' * 40}\n".encode())
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x01" + pack[:midpoint])
        + pkt_line(b"\x02counting objects\n")
        + pkt_line(b"\x01" + pack[midpoint:])
        + b"0000"
    )

    parsed = parse_fetch_response(data)

    assert parsed.shallow == ("c" * 40,)
    assert parsed.pack == pack


def test_parse_packfile_fatal_sideband_raises():
    data = (
        pkt_line(b"packfile\n")
        + pkt_line(b"\x03remote exploded\n")
        + b"0000"
    )
    with pytest.raises(RuntimeError, match="remote exploded"):
        parse_fetch_response(data)


def test_v2_fetch_client_posts_fetch_command_and_parses_pack(monkeypatch):
    blob = _blob_native(b"payload\n")
    pack = build_pack([blob])
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack) + b"0000"

    def fake_urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    client._capabilities = _caps()
    advertisement = Advertisement(
        {"refs/heads/main": "a" * 40},
        {"fetch=wait-for-done", "ls-refs=unborn"},
        {},
    )

    result = client.fetch(haves={"b" * 40}, advertisement=advertisement)

    assert result.objects[blob.oid].data == b"payload\n"
    assert requests[0].full_url.endswith("/git-upload-pack")
    assert pkt_line(b"command=fetch\n") in requests[0].data
    assert pkt_line(b"done\n") in requests[0].data


def test_protocol_v2_transport_falls_back_to_v0_discovery(monkeypatch):
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    v0_calls = []

    def fake_v0(self):
        v0_calls.append(self.url)
        return expected

    def unavailable(self, *args, **kwargs):
        raise ProtocolV2Unavailable("no v2")

    monkeypatch.setattr(SmartHttpClient, "discover", fake_v0)
    monkeypatch.setattr(SmartHttpV2FetchClient, "discover", unavailable)

    with protocol_v2_transport():
        actual = SmartHttpClient("https://example.test/repo.git").discover()

    assert actual is expected
    assert v0_calls == ["https://example.test/repo.git"]


def test_protocol_v2_transport_routes_existing_client_api(monkeypatch):
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    calls = []

    def fake_v2(self, *args, **kwargs):
        calls.append(self.url)
        return expected

    monkeypatch.setattr(SmartHttpV2FetchClient, "discover", fake_v2)

    with protocol_v2_transport():
        actual = SmartHttpClient("https://example.test/repo.git").discover()

    assert actual is expected
    assert calls == ["https://example.test/repo.git"]


def test_negotiate_only_returns_sha256_common_commit(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    tip = _commit(repo)
    repo.add_remote("origin", "https://example.test/repo.git")
    seen = {}

    def fake_discover(self):
        return Advertisement({"refs/heads/main": "f" * 40}, set(), {})

    def fake_negotiate(self, *, haves, advertisement=None):
        seen["haves"] = set(haves)
        return (next(iter(seen["haves"])),)

    monkeypatch.setattr(SmartHttpV2FetchClient, "discover", fake_discover)
    monkeypatch.setattr(SmartHttpV2FetchClient, "negotiate", fake_negotiate)

    common = negotiate_only(repo, source="origin", restrict=["main"])

    assert common == [tip]
    assert all(len(oid) == 40 for oid in seen["haves"])


def test_negotiate_only_requires_restriction(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    with pytest.raises(RuntimeError, match="requires at least one"):
        negotiate_only(repo, source="origin", restrict=[])


def test_run_fetch_negotiate_only_prints_common_sha256(tmp_path, monkeypatch, capsys):
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


def test_protocol_version_two_wraps_ordinary_fetch(monkeypatch, tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.config_set("protocol", "version", "2")
    called = []

    def fake_discover(self, *args, **kwargs):
        called.append("v2")
        return Advertisement({"refs/heads/main": "a" * 40}, set(), {})

    def fake_inner(argv):
        SmartHttpClient("https://example.test/repo.git").discover()
        return 0

    monkeypatch.setattr(SmartHttpV2FetchClient, "discover", fake_discover)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", fake_inner)
    monkeypatch.chdir(repo.worktree)

    assert run_fetch([]) == 0
    assert called == ["v2"]
