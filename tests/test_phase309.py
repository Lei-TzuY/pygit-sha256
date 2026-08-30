from __future__ import annotations

from email.message import Message

import pytest

from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
)
from pygit.protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    SmartHttpV2FetchClient,
    parse_fetch_response,
)
from pygit.remote import Advertisement, build_pack, pkt_line


class _Response:
    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.read_calls = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


def _capabilities() -> bytes:
    return (
        pkt_line(b"version 2")
        + pkt_line(b'agent=phase309~agent|quote"slash\\tick`')
        + pkt_line(b"ls-refs=unborn")
        + pkt_line(b"fetch=shallow wait-for-done")
        + pkt_line(b"object-format=sha1")
        + b"0000"
    )


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "fetch": "wait-for-done",
            "object-format": "sha1",
        }
    )


def test_full_fetch_composes_capability_ls_refs_and_fetch_state_machines(monkeypatch):
    head = "a" * 40
    pack = build_pack([])
    discovery = _Response(
        _capabilities(),
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    refs = _Response(
        pkt_line(f"{head} HEAD symref-target:refs/heads/main".encode())
        + pkt_line(f"{head} refs/heads/main".encode())
        + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    fetch = _Response(
        pkt_line(b"packfile") + pkt_line(b"\x01" + pack) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([discovery, refs, fetch])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert result is not None
    assert result.advertisement.refs["refs/heads/main"] == head
    assert result.advertisement.symrefs["HEAD"] == "refs/heads/main"
    assert result.objects == {}
    assert discovery.read_calls == refs.read_calls == fetch.read_calls == 1
    assert len(requests) == 3
    assert requests[0].headers["Git-protocol"] == "version=2"
    assert pkt_line(b"command=ls-refs\n") in requests[1].data
    assert pkt_line(b"command=fetch\n") in requests[2].data
    assert pkt_line(f"want {head}\n".encode()) in requests[2].data
    assert pkt_line(b"done\n") in requests[2].data


def test_duplicate_ls_refs_record_stops_before_fetch_post(monkeypatch):
    head = "b" * 40
    discovery = _Response(
        _capabilities(),
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    refs = _Response(
        pkt_line(f"{head} refs/heads/main".encode())
        + pkt_line(f"{head} refs/heads/main".encode())
        + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([discovery, refs])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="Duplicate protocol-v2 ls-refs result"):
        SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert discovery.read_calls == 1
    assert refs.read_calls == 1
    assert len(requests) == 2


def test_ready_requires_packfile_in_same_wire_response():
    common = "c" * 40
    body = (
        pkt_line(b"acknowledgments")
        + pkt_line(f"ACK {common}".encode())
        + pkt_line(b"ready")
        + b"0000"
    )

    with pytest.raises(ValueError, match="requires a packfile in the same response"):
        parse_fetch_response(body)


def test_ready_followed_by_packfile_is_valid_wire_state():
    common = "d" * 40
    pack = build_pack([])
    body = (
        pkt_line(b"acknowledgments")
        + pkt_line(f"ACK {common}".encode())
        + pkt_line(b"ready")
        + b"0001"
        + pkt_line(b"packfile")
        + pkt_line(b"\x01" + pack)
        + b"0000"
    )

    parsed = parse_fetch_response(body)
    assert parsed.acknowledgments == (common,)
    assert parsed.ready is True
    assert parsed.pack == pack


def test_negotiate_preserves_public_pack_transition_runtime_error(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "e" * 40}, set(), {})
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

    with pytest.raises(RuntimeError, match="unexpectedly advanced to pack transfer"):
        client.negotiate(haves=["f" * 40], advertisement=advertisement)
