from __future__ import annotations

import os
import subprocess
from email.message import Message

import pytest

from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    build_ls_refs_request,
    parse_capability_advertisement,
    parse_ls_refs_response,
)
from pygit.protocol_v2_fetch import SmartHttpV2FetchClient, parse_fetch_response
from pygit.remote import build_pack, pkt_line


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


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "agent": "git/2.55.0",
            "ls-refs": "unborn",
            "fetch": "shallow wait-for-done",
            "object-format": "sha1",
        }
    )


def _advertisement() -> bytes:
    return (
        pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/2.55.0\n")
        + pkt_line(b"ls-refs=unborn\n")
        + pkt_line(b"fetch=shallow wait-for-done\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000"
    )


def test_v2_capability_advertisement_requires_exact_flush_envelope():
    body = pkt_line(b"version 2\n") + pkt_line(b"ls-refs\n")
    with pytest.raises(ValueError, match="did not end with flush"):
        parse_capability_advertisement(body)

    with pytest.raises(ValueError, match="non-flush terminator"):
        parse_capability_advertisement(body + b"0002")

    with pytest.raises(ValueError, match="Trailing data"):
        parse_capability_advertisement(body + b"0000" + pkt_line(b"evil\n"))


def test_v0_advertisement_remains_an_explicit_fallback():
    oid = "a" * 40
    v0 = (
        pkt_line(b"# service=git-upload-pack\n")
        + b"0000"
        + pkt_line(f"{oid} HEAD\x00multi_ack\n".encode())
        + b"0000"
    )
    assert parse_capability_advertisement(v0) is None


def test_ls_refs_requires_exact_flush_envelope():
    oid = "b" * 40
    line = pkt_line(f"{oid} refs/heads/main\n".encode())

    with pytest.raises(ValueError, match="did not end with flush"):
        parse_ls_refs_response(line, _caps())

    with pytest.raises(ValueError, match="non-flush terminator"):
        parse_ls_refs_response(line + b"0002", _caps())

    with pytest.raises(ValueError, match="Trailing data"):
        parse_ls_refs_response(line + b"0000" + pkt_line(b"evil\n"), _caps())


def test_fetch_ls_refs_wrong_mime_fails_before_body_read(monkeypatch):
    response = _Response(pkt_line(b"a" * 40 + b" refs/heads/main\n") + b"0000", "text/html")
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: response)

    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="Content-Type"):
        client._discover_refs_with_capabilities(_caps())
    assert response.read_calls == 0


def test_fetch_post_wrong_mime_fails_before_body_read(monkeypatch):
    pack = build_pack([])
    response = _Response(
        pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack) + b"0000",
        "text/plain",
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: response)

    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="Content-Type"):
        client._post_fetch(b"request")
    assert response.read_calls == 0


def test_valid_fetch_mime_does_not_bypass_strict_pkt_line_framing(monkeypatch):
    response = _Response(
        pkt_line(b"acknowledgments\n") + pkt_line(b"NAK\n"),
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: response)

    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    with pytest.raises(ValueError, match="did not end with flush"):
        client._post_fetch(b"request")
    assert response.read_calls == 1


def test_full_smart_http_fetch_checks_all_three_response_envelopes(monkeypatch):
    head = "c" * 40
    pack = build_pack([])
    discovery = _Response(_advertisement(), _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE)
    refs = _Response(
        pkt_line(f"{head} HEAD symref-target:refs/heads/main\n".encode())
        + pkt_line(f"{head} refs/heads/main\n".encode())
        + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    fetch = _Response(
        pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([discovery, refs, fetch])
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: next(responses),
    )

    result = SmartHttpV2FetchClient("https://example.test/repo.git").fetch()
    assert result is not None
    assert result.advertisement.refs["refs/heads/main"] == head
    assert result.objects == {}
    assert discovery.read_calls == refs.read_calls == fetch.read_calls == 1


def test_native_git_v2_advertisement_and_ls_refs_have_strict_flush_envelopes(tmp_path):
    repo = tmp_path / "native"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Phase301"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "phase301@example.test"],
        check=True,
    )
    (repo / "file.txt").write_text("native\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "native"], check=True, capture_output=True)

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    advertised = subprocess.run(
        ["git", "upload-pack", "--stateless-rpc", "--advertise-refs", str(repo)],
        env=env,
        check=True,
        capture_output=True,
    ).stdout
    assert advertised.endswith(b"0000")
    capabilities = parse_capability_advertisement(advertised)
    assert capabilities is not None
    assert capabilities.supports("ls-refs")

    request = build_ls_refs_request(capabilities)
    response = subprocess.run(
        ["git", "upload-pack", "--stateless-rpc", str(repo)],
        env=env,
        input=request,
        check=True,
        capture_output=True,
    ).stdout
    assert response.endswith(b"0000")
    refs = parse_ls_refs_response(response, capabilities)
    expected = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert refs.refs["refs/heads/main"] == expected


def test_strict_fetch_parser_still_accepts_valid_ack_only_response():
    response = pkt_line(b"acknowledgments\n") + pkt_line(b"NAK\n") + b"0000"
    parsed = parse_fetch_response(response)
    assert parsed.nak is True
    assert parsed.pack is None
