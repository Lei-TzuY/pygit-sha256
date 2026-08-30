from __future__ import annotations

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    SmartHttpV2FetchClient,
    parse_fetch_response,
)
from pygit.remote import Advertisement, build_pack, pkt_line


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "fetch": "wait-for-done",
            "object-format": "sha1",
        }
    )


def test_ready_ack_requires_packfile_in_same_response():
    common = "a" * 40
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {common}\n".encode())
        + pkt_line(b"ready\n")
        + b"0000"
    )

    with pytest.raises(ValueError, match="requires a packfile in the same response"):
        parse_fetch_response(body)


def test_ready_ack_with_packfile_is_valid_section_transition():
    common = "b" * 40
    pack = build_pack([])
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {common}\n".encode())
        + pkt_line(b"ready\n")
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x01" + pack)
        + b"0000"
    )

    parsed = parse_fetch_response(body)
    assert parsed.acknowledgments == (common,)
    assert parsed.ready is True
    assert parsed.pack == pack


def test_wait_for_done_negotiate_reports_ready_pack_as_protocol_error(monkeypatch):
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

    with pytest.raises(ValueError, match="wait-for-done response must not contain ready"):
        client.negotiate(haves=["b" * 40], advertisement=advertisement)
