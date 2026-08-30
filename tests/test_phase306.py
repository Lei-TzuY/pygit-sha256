from __future__ import annotations

from email.message import Message

import pytest

from pygit.protocol_v2 import (
    _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
)
from pygit.protocol_v2_object_info import SmartHttpV2ObjectInfoClient
from pygit.remote import pkt_line


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


def _valid_capabilities() -> bytes:
    return (
        pkt_line(b"version 2")
        + pkt_line(b'agent=phase306~agent|quote"slash\\tick`')
        + pkt_line(b"object-info")
        + pkt_line(b"object-format=sha1")
        + b"0000"
    )


def test_object_info_query_composes_strict_capability_and_record_grammar(monkeypatch):
    oid = "a" * 40
    discovery = _Response(
        _valid_capabilities(),
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    object_info = _Response(
        pkt_line(b"size") + pkt_line(f"{oid} 42".encode()) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([discovery, object_info])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = SmartHttpV2ObjectInfoClient(
        "https://example.test/repo.git"
    ).query_sizes([oid])

    assert result == {oid: 42}
    assert discovery.read_calls == 1
    assert object_info.read_calls == 1
    assert len(requests) == 2
    assert requests[0].headers["Git-protocol"] == "version=2"
    assert pkt_line(b"command=object-info\n") in requests[1].data
    assert pkt_line(f"oid {oid}\n".encode()) in requests[1].data


def test_malformed_capability_record_blocks_object_info_post(monkeypatch):
    oid = "b" * 40
    discovery = _Response(
        pkt_line(b"version 2\n")
        + pkt_line(b"object-info\n")
        + pkt_line(b"future=value|invalid\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000",
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return discovery

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(ValueError, match="capability value"):
        SmartHttpV2ObjectInfoClient(
            "https://example.test/repo.git"
        ).query_sizes([oid])

    assert discovery.read_calls == 1
    assert len(requests) == 1
    assert requests[0].data is None


def test_valid_capabilities_do_not_weaken_object_info_record_validation(monkeypatch):
    oid = "c" * 40
    discovery = _Response(
        _valid_capabilities(),
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    malformed = _Response(
        pkt_line(b"size") + pkt_line(f"{oid} 42 extra".encode()) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([discovery, malformed])

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: next(responses),
    )

    with pytest.raises(ValueError, match="Malformed protocol-v2 object-info result"):
        SmartHttpV2ObjectInfoClient(
            "https://example.test/repo.git"
        ).query_sizes([oid])

    assert discovery.read_calls == 1
    assert malformed.read_calls == 1
