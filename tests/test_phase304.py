from __future__ import annotations

import pytest

from pygit.protocol_v2_object_info import (
    ObjectSizeInfo,
    parse_object_info_size_response,
)
from pygit.remote import pkt_line


def _response(*records: bytes) -> bytes:
    return b"".join(pkt_line(record) for record in records) + b"0000"


def test_object_info_accepts_native_git_no_lf_records():
    known = "a" * 40
    missing = "b" * 40

    assert parse_object_info_size_response(
        _response(b"size", f"{known} 17".encode(), f"{missing} ".encode())
    ) == (
        ObjectSizeInfo(known, 17),
        ObjectSizeInfo(missing, None),
    )


def test_object_info_accepts_documented_single_lf_records():
    oid = "c" * 40

    assert parse_object_info_size_response(
        _response(b"size\n", f"{oid} 23\n".encode())
    ) == (ObjectSizeInfo(oid, 23),)


def test_object_info_rejects_crlf_and_extra_newline():
    oid = "d" * 40

    with pytest.raises(ValueError, match="Malformed line ending"):
        parse_object_info_size_response(_response(b"size\r\n", f"{oid} 1".encode()))

    with pytest.raises(ValueError, match="Malformed line ending"):
        parse_object_info_size_response(_response(b"size\n\n", f"{oid} 1".encode()))

    with pytest.raises(ValueError, match="Malformed line ending"):
        parse_object_info_size_response(
            _response(b"size", f"{oid} 1\n\n".encode())
        )


def test_object_info_rejects_extra_field_spacing():
    oid = "e" * 40

    with pytest.raises(ValueError, match="Malformed protocol-v2 object-info result"):
        parse_object_info_size_response(_response(b"size", f"{oid}  1".encode()))

    with pytest.raises(ValueError, match="Malformed protocol-v2 object-info result"):
        parse_object_info_size_response(_response(b"size", f"{oid} 1 ".encode()))


def test_object_info_rejects_non_ascii_record_text():
    with pytest.raises(ValueError, match="Invalid ASCII"):
        parse_object_info_size_response(_response(b"size", b"\xff 1"))
