from __future__ import annotations

import pytest

from pygit.protocol_v2_object_info import (
    ObjectSizeInfo,
    parse_object_info_size_response,
)
from pygit.remote import pkt_line


def _response(*records: bytes) -> bytes:
    return b"".join(pkt_line(record) for record in records) + b"0000"


def test_object_info_requires_lf_on_attribute_record():
    oid = "a" * 40

    with pytest.raises(ValueError, match="record did not end with LF"):
        parse_object_info_size_response(_response(b"size", f"{oid} 1\n".encode()))


def test_object_info_requires_lf_on_object_record():
    oid = "b" * 40

    with pytest.raises(ValueError, match="record did not end with LF"):
        parse_object_info_size_response(_response(b"size\n", f"{oid} 1".encode()))


def test_object_info_rejects_crlf_and_extra_newline():
    oid = "c" * 40

    with pytest.raises(ValueError, match="Malformed line ending"):
        parse_object_info_size_response(_response(b"size\r\n", f"{oid} 1\n".encode()))

    with pytest.raises(ValueError, match="Malformed line ending"):
        parse_object_info_size_response(_response(b"size\n\n", f"{oid} 1\n".encode()))


def test_object_info_rejects_extra_field_spacing():
    oid = "d" * 40

    with pytest.raises(ValueError, match="Malformed protocol-v2 object-info result"):
        parse_object_info_size_response(_response(b"size\n", f"{oid}  1\n".encode()))

    with pytest.raises(ValueError, match="Malformed protocol-v2 object-info result"):
        parse_object_info_size_response(_response(b"size\n", f"{oid} 1 \n".encode()))


def test_object_info_accepts_exact_git_textual_grammar():
    known = "e" * 40
    missing = "f" * 40

    assert parse_object_info_size_response(
        _response(
            b"size\n",
            f"{known} 17\n".encode(),
            f"{missing} \n".encode(),
        )
    ) == (
        ObjectSizeInfo(known, 17),
        ObjectSizeInfo(missing, None),
    )
