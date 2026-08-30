from __future__ import annotations

from email.message import Message

import pytest

from pygit.protocol_v2 import (
    SmartHttpV2QueryClient,
    _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    _UPLOAD_PACK_REQUEST_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
)
from pygit.remote import pkt_line


class _Response:
    def __init__(self, body: bytes, content_type=...):
        self.body = body
        self.read_calls = 0
        self.headers = Message()
        if content_type is not ...:
            self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


class _HeaderlessResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


def _v2_advertisement() -> bytes:
    return (
        pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/2.55.0\n")
        + pkt_line(b"object-format=sha1\n")
        + pkt_line(b"ls-refs=unborn\n")
        + b"0000"
    )


def _ls_refs_body(oid: str) -> bytes:
    return pkt_line(f"{oid} refs/heads/main\n".encode()) + b"0000"


@pytest.mark.parametrize(
    "content_type",
    [
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
        "Application/X-Git-Upload-Pack-Advertisement; charset=utf-8",
    ],
)
def test_discovery_accepts_smart_advertisement_media_type(monkeypatch, content_type):
    response = _Response(_v2_advertisement(), content_type)
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request, timeout))
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    capabilities = SmartHttpV2QueryClient(
        "https://example.test/repo.git",
        timeout=11,
    ).discover_capabilities()

    assert capabilities is not None
    assert capabilities.supports("ls-refs")
    assert response.read_calls == 1
    assert seen[0][1] == 11
    assert seen[0][0].headers["Accept"] == _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE
    assert seen[0][0].headers["Git-protocol"] == "version=2"


@pytest.mark.parametrize("content_type", ["text/plain", ...])
def test_discovery_non_smart_media_type_returns_fallback_without_read(
    monkeypatch,
    content_type,
):
    response = _Response(_v2_advertisement(), content_type)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: response,
    )

    assert SmartHttpV2QueryClient(
        "https://example.test/repo.git"
    ).discover_capabilities() is None
    assert response.read_calls == 0


def test_discovery_headerless_legacy_response_still_parses(monkeypatch):
    response = _HeaderlessResponse(_v2_advertisement())
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: response,
    )

    capabilities = SmartHttpV2QueryClient(
        "https://example.test/repo.git"
    ).discover_capabilities()

    assert capabilities is not None
    assert capabilities.supports("ls-refs")
    assert response.read_calls == 1


def test_ls_refs_accepts_upload_pack_result_media_type(monkeypatch):
    oid = "a" * 40
    discovery = _Response(
        _v2_advertisement(),
        "Application/X-Git-Upload-Pack-Advertisement; charset=binary",
    )
    result = _Response(
        _ls_refs_body(oid),
        "Application/X-Git-Upload-Pack-Result; charset=binary",
    )
    responses = iter([discovery, result])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    advertisement = SmartHttpV2QueryClient(
        "https://example.test/repo.git"
    ).discover_refs()

    assert advertisement is not None
    assert advertisement.refs["refs/heads/main"] == oid
    assert discovery.read_calls == 1
    assert result.read_calls == 1
    assert requests[1].headers["Accept"] == _UPLOAD_PACK_RESULT_MEDIA_TYPE
    assert requests[1].headers["Content-type"] == _UPLOAD_PACK_REQUEST_MEDIA_TYPE


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", ...])
def test_ls_refs_rejects_wrong_or_missing_result_media_type_before_read(
    monkeypatch,
    content_type,
):
    oid = "b" * 40
    discovery = _Response(_v2_advertisement(), _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE)
    result = _Response(_ls_refs_body(oid), content_type)
    responses = iter([discovery, result])

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: next(responses),
    )

    client = SmartHttpV2QueryClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="upload-pack response Content-Type"):
        client.discover_refs()

    assert discovery.read_calls == 1
    assert result.read_calls == 0


def test_ls_refs_headerless_legacy_responses_remain_compatible(monkeypatch):
    oid = "c" * 40
    discovery = _HeaderlessResponse(_v2_advertisement())
    result = _HeaderlessResponse(_ls_refs_body(oid))
    responses = iter([discovery, result])

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: next(responses),
    )

    advertisement = SmartHttpV2QueryClient(
        "https://example.test/repo.git"
    ).discover_refs()

    assert advertisement is not None
    assert advertisement.refs["refs/heads/main"] == oid
    assert discovery.read_calls == 1
    assert result.read_calls == 1
