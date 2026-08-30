from __future__ import annotations

from email.message import Message

import pytest

from pygit.protocol_v2 import (
    _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
)
from pygit.protocol_v2_fetch import SmartHttpV2FetchClient
from pygit.remote import build_pack, pkt_line


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
        + pkt_line(b'agent=phase308~agent|quote"slash\\tick`')
        + pkt_line(b"ls-refs=unborn")
        + pkt_line(b"fetch=shallow wait-for-done")
        + pkt_line(b"object-info")
        + pkt_line(b"object-format=sha1")
        + b"0000"
    )


def _refs(oid: str) -> bytes:
    return pkt_line(f"{oid} refs/heads/main".encode()) + b"0000"


def _response(body: bytes, *, advertisement: bool = False) -> _Response:
    return _Response(
        body,
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE
        if advertisement
        else _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )


def test_fetch_composes_strict_capability_refs_and_fetch_state_machine(monkeypatch):
    oid = "a" * 40
    pack = build_pack([])
    discovery = _response(_capabilities(), advertisement=True)
    refs = _response(_refs(oid))
    fetch = _response(
        pkt_line(b"packfile") + pkt_line(b"\x01" + pack) + b"0000"
    )
    responses = iter([discovery, refs, fetch])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert result is not None
    assert result.advertisement.refs == {"refs/heads/main": oid}
    assert result.objects == {}
    assert discovery.read_calls == refs.read_calls == fetch.read_calls == 1
    assert len(requests) == 3
    assert requests[0].data is None
    assert requests[0].headers["Git-protocol"] == "version=2"
    assert pkt_line(b"command=ls-refs\n") in requests[1].data
    assert pkt_line(b"command=fetch\n") in requests[2].data
    assert pkt_line(f"want {oid}\n".encode()) in requests[2].data
    assert pkt_line(b"done\n") in requests[2].data


def test_malformed_capability_blocks_refs_and_fetch_posts(monkeypatch):
    discovery = _response(
        pkt_line(b"version 2\n")
        + pkt_line(b"ls-refs\n")
        + pkt_line(b"fetch=wait-for-done\n")
        + pkt_line(b"future=value|invalid\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000",
        advertisement=True,
    )
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return discovery

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="capability value"):
        SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert discovery.read_calls == 1
    assert len(requests) == 1
    assert requests[0].data is None


def test_duplicate_ls_refs_blocks_fetch_post(monkeypatch):
    oid = "b" * 40
    discovery = _response(_capabilities(), advertisement=True)
    duplicate_refs = _response(
        pkt_line(f"{oid} refs/heads/main\n".encode())
        + pkt_line(f"{oid} refs/heads/main\n".encode())
        + b"0000"
    )
    unused_fetch = _response(pkt_line(b"packfile\n") + b"0000")
    responses = iter([discovery, duplicate_refs, unused_fetch])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="Duplicate protocol-v2 ls-refs result"):
        SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert discovery.read_calls == duplicate_refs.read_calls == 1
    assert unused_fetch.read_calls == 0
    assert len(requests) == 2
    assert pkt_line(b"command=ls-refs\n") in requests[1].data


def test_valid_discovery_and_refs_do_not_weaken_fetch_ready_contract(monkeypatch):
    oid = "c" * 40
    discovery = _response(_capabilities(), advertisement=True)
    refs = _response(_refs(oid))
    malformed_fetch = _response(
        pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {oid}\n".encode())
        + pkt_line(b"ready\n")
        + b"0000"
    )
    responses = iter([discovery, refs, malformed_fetch])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="requires a packfile in the same response"):
        SmartHttpV2FetchClient("https://example.test/repo.git").fetch()

    assert discovery.read_calls == refs.read_calls == malformed_fetch.read_calls == 1
    assert len(requests) == 3
    assert pkt_line(b"command=fetch\n") in requests[2].data


def test_wait_for_done_negotiation_composes_with_strict_discovery(monkeypatch):
    oid = "d" * 40
    discovery = _response(_capabilities(), advertisement=True)
    refs = _response(_refs(oid))
    ack = _response(
        pkt_line(b"acknowledgments")
        + pkt_line(f"ACK {oid}".encode())
        + b"0000"
    )
    responses = iter([discovery, refs, ack])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    commons = SmartHttpV2FetchClient("https://example.test/repo.git").negotiate(
        haves=[oid]
    )

    assert commons == (oid,)
    assert discovery.read_calls == refs.read_calls == ack.read_calls == 1
    assert len(requests) == 3
    body = requests[2].data
    assert pkt_line(b"wait-for-done\n") in body
    assert pkt_line(f"have {oid}\n".encode()) in body
    assert pkt_line(b"done\n") not in body
