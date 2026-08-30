from __future__ import annotations

import pytest

from pygit.protocol_v2_fetch import parse_fetch_response
from pygit.remote import build_pack, pkt_line


def _response(*sections: tuple[str, bytes]) -> bytes:
    body = b""
    for index, (name, payload) in enumerate(sections):
        if index:
            body += b"0001"
        body += pkt_line((name + "\n").encode("ascii"))
        body += payload
    return body + b"0000"


def _wanted(oid: str) -> bytes:
    return pkt_line(f"{oid} refs/heads/main\n".encode("ascii"))


def _shallow(oid: str) -> bytes:
    return pkt_line(f"shallow {oid}\n".encode("ascii"))


def _pack() -> bytes:
    return pkt_line(b"\x01" + build_pack([]))


def test_fetch_parser_accepts_documented_shallow_then_wanted_refs_order():
    shallow_oid = "a" * 40
    wanted_oid = "b" * 40
    parsed = parse_fetch_response(
        _response(
            ("shallow-info", _shallow(shallow_oid)),
            ("wanted-refs", _wanted(wanted_oid)),
            ("packfile", _pack()),
        )
    )

    assert parsed.shallow == (shallow_oid,)
    assert parsed.wanted_refs == {"refs/heads/main": wanted_oid}
    assert parsed.pack is not None


def test_fetch_parser_accepts_native_git_wanted_refs_then_shallow_order():
    shallow_oid = "c" * 40
    wanted_oid = "d" * 40
    parsed = parse_fetch_response(
        _response(
            ("wanted-refs", _wanted(wanted_oid)),
            ("shallow-info", _shallow(shallow_oid)),
            ("packfile", _pack()),
        )
    )

    assert parsed.shallow == (shallow_oid,)
    assert parsed.wanted_refs == {"refs/heads/main": wanted_oid}
    assert parsed.pack is not None


def test_fetch_parser_still_rejects_acknowledgments_after_metadata():
    wanted_oid = "e" * 40
    ack_oid = "f" * 40
    with pytest.raises(ValueError, match="Out-of-order protocol-v2 fetch section: acknowledgments"):
        parse_fetch_response(
            _response(
                ("wanted-refs", _wanted(wanted_oid)),
                ("acknowledgments", pkt_line(f"ACK {ack_oid}\n".encode("ascii"))),
                ("packfile", _pack()),
            )
        )


def test_fetch_parser_still_rejects_metadata_after_packfile():
    wanted_oid = "1" * 40
    shallow_oid = "2" * 40
    with pytest.raises(ValueError, match="Unexpected delimiter after protocol-v2 packfile section"):
        parse_fetch_response(
            _response(
                ("wanted-refs", _wanted(wanted_oid)),
                ("packfile", _pack()),
                ("shallow-info", _shallow(shallow_oid)),
            )
        )
