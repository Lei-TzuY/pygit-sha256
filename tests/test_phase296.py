from __future__ import annotations

from email.message import Message

import pytest

from pygit.protocol_v2_object_info import (
    ObjectSizeInfo,
    SmartHttpV2ObjectInfoClient,
)
from pygit.remote import pkt_line


def _object_info_body(oid: str, size: int = 17) -> bytes:
    return pkt_line(b"size\n") + pkt_line(f"{oid} {size}\n".encode()) + b"0000"


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


@pytest.mark.parametrize(
    "content_type",
    [
        "application/x-git-upload-pack-result",
        "Application/X-Git-Upload-Pack-Result; charset=binary",
    ],
)
def test_object_info_post_accepts_upload_pack_result_media_type(
    monkeypatch,
    content_type,
):
    oid = "a" * 40
    response = _Response(_object_info_body(oid, 23), content_type)
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request, timeout))
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = SmartHttpV2ObjectInfoClient("https://example.test/repo.git", timeout=9)
    assert client._post_object_info(b"request") == (ObjectSizeInfo(oid, 23),)
    assert response.read_calls == 1
    assert seen[0][1] == 9
    assert seen[0][0].headers["Accept"] == "application/x-git-upload-pack-result"
    assert seen[0][0].headers["Content-type"] == "application/x-git-upload-pack-request"


def test_object_info_post_rejects_wrong_media_type_before_read(monkeypatch):
    oid = "b" * 40
    response = _Response(_object_info_body(oid), "text/html; charset=utf-8")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: response,
    )

    client = SmartHttpV2ObjectInfoClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="upload-pack response Content-Type.*text/html"):
        client._post_object_info(b"request")

    assert response.read_calls == 0


def test_object_info_post_rejects_missing_media_type_before_read(monkeypatch):
    oid = "c" * 40
    response = _Response(_object_info_body(oid))

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: response,
    )

    client = SmartHttpV2ObjectInfoClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="Content-Type.*<missing>"):
        client._post_object_info(b"request")

    assert response.read_calls == 0


def test_valid_media_type_does_not_bypass_phase294_response_framing(monkeypatch):
    oid = "d" * 40
    truncated = pkt_line(b"size\n") + pkt_line(f"{oid} 31\n".encode())
    response = _Response(truncated, "application/x-git-upload-pack-result")

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: response,
    )

    client = SmartHttpV2ObjectInfoClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="did not end with flush packet"):
        client._post_object_info(b"request")

    assert response.read_calls == 1


def test_headerless_legacy_test_double_remains_compatible(monkeypatch):
    oid = "e" * 40

    class LegacyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return _object_info_body(oid, 41)

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: LegacyResponse(),
    )

    client = SmartHttpV2ObjectInfoClient("https://example.test/repo.git")
    assert client._post_object_info(b"request") == (ObjectSizeInfo(oid, 41),)
