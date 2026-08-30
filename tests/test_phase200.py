from __future__ import annotations

from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import (
    SmartHttpV2FetchClient,
    build_fetch_request,
    parse_fetch_response,
)
from pygit.remote import build_pack, pkt_line


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "agent": "git/2.47.3",
            "ls-refs": "unborn",
            "fetch": "shallow wait-for-done ref-in-want",
            "object-format": "sha1",
        }
    )


def test_build_fetch_request_frames_command_wants_haves_and_done():
    want_a = "a" * 40
    want_b = "b" * 40
    have = "c" * 40
    body = build_fetch_request(
        _caps(),
        [want_b, want_a, want_a],
        haves=[have, have],
    )

    assert body.startswith(pkt_line(b"command=fetch\n"))
    assert pkt_line(b"agent=pygit/0.1\n") in body
    assert b"0001" in body
    assert pkt_line(b"no-progress\n") in body
    assert pkt_line(b"ofs-delta\n") in body
    assert pkt_line(f"want {want_a}\n".encode()) in body
    assert pkt_line(f"want {want_b}\n".encode()) in body
    assert body.count(pkt_line(f"want {want_a}\n".encode())) == 1
    assert body.count(pkt_line(f"have {have}\n".encode())) == 1
    assert pkt_line(b"done\n") in body
    assert body.endswith(b"0000")
    assert pkt_line(b"thin-pack\n") not in body


def test_build_fetch_request_rejects_invalid_object_ids():
    try:
        build_fetch_request(_caps(), ["not-an-oid"])
    except ValueError as exc:
        assert "40-hex" in str(exc)
    else:
        raise AssertionError("invalid want should fail before transport")


def test_parse_acknowledgment_only_response():
    have = "a" * 40
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {have}\n".encode())
        + b"0000"
    )
    parsed = parse_fetch_response(body)
    assert parsed.acknowledgments == (have,)
    assert parsed.ready is False
    assert parsed.nak is False
    assert parsed.pack is None


def test_parse_full_fetch_sections_and_sideband_pack():
    shallow = "a" * 40
    unshallow = "b" * 40
    wanted = "c" * 40
    pack = build_pack([])
    midpoint = len(pack) // 2
    body = (
        pkt_line(b"shallow-info\n")
        + pkt_line(f"shallow {shallow}\n".encode())
        + pkt_line(f"unshallow {unshallow}\n".encode())
        + b"0001"
        + pkt_line(b"wanted-refs\n")
        + pkt_line(f"{wanted} refs/heads/main\n".encode())
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x02counting objects\n")
        + pkt_line(b"\x01" + pack[:midpoint])
        + pkt_line(b"\x01" + pack[midpoint:])
        + b"0000"
    )
    parsed = parse_fetch_response(body)
    assert parsed.shallow == (shallow,)
    assert parsed.unshallow == (unshallow,)
    assert parsed.wanted_refs == {"refs/heads/main": wanted}
    assert parsed.pack == pack


def test_parse_fetch_rejects_remote_sideband_error():
    body = (
        pkt_line(b"packfile\n")
        + pkt_line(b"\x03remote exploded\n")
        + b"0000"
    )
    try:
        parse_fetch_response(body)
    except RuntimeError as exc:
        assert "remote exploded" in str(exc)
    else:
        raise AssertionError("fatal sideband must fail fetch")


def test_parse_fetch_rejects_unrequested_packfile_uris():
    body = pkt_line(b"packfile-uris\n") + b"0000"
    try:
        parse_fetch_response(body)
    except RuntimeError as exc:
        assert "packfile-uris" in str(exc)
    else:
        raise AssertionError("unexpected packfile URI response must not be ignored")


def test_v2_fetch_http_exchange_discovers_refs_and_parses_pack(monkeypatch):
    head = "a" * 40
    capabilities = (
        pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/2.47.3\n")
        + pkt_line(b"ls-refs=unborn\n")
        + pkt_line(b"fetch=shallow wait-for-done\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000"
    )
    refs = (
        pkt_line(f"{head} HEAD symref-target:refs/heads/main\n".encode())
        + pkt_line(f"{head} refs/heads/main\n".encode())
        + b"0000"
    )
    pack = build_pack([])
    fetch_response = pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack) + b"0000"
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    responses = iter(
        [Response(capabilities), Response(refs), Response(fetch_response)]
    )

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert result is not None
    assert result.advertisement.refs["refs/heads/main"] == head
    assert result.objects == {}
    assert len(requests) == 3
    assert requests[0].headers["Git-protocol"] == "version=2"
    assert pkt_line(b"command=ls-refs\n") in requests[1].data
    assert pkt_line(b"command=fetch\n") in requests[2].data
    assert pkt_line(f"want {head}\n".encode()) in requests[2].data
    assert pkt_line(b"done\n") in requests[2].data


def test_v2_fetch_returns_none_when_server_ignores_protocol_v2(monkeypatch):
    head = "a" * 40
    v0 = (
        pkt_line(b"# service=git-upload-pack\n")
        + b"0000"
        + pkt_line(f"{head} HEAD\x00multi_ack\n".encode())
        + b"0000"
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return v0

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    result = SmartHttpV2FetchClient("https://example.test/repo.git").fetch()
    assert result is None
