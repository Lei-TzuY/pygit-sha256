from __future__ import annotations

import pytest

from pygit.protocol_v2_object_info import (
    ObjectSizeInfo,
    parse_object_info_size_response,
)
from pygit.remote import pkt_line


def _valid_prefix(oid: str, size: int = 17) -> bytes:
    return pkt_line(b"size\n") + pkt_line(f"{oid} {size}\n".encode())


def test_object_info_response_requires_flush_packet():
    oid = "a" * 40

    with pytest.raises(ValueError, match="did not end with flush packet"):
        parse_object_info_size_response(_valid_prefix(oid))


def test_object_info_response_rejects_response_end_as_terminator():
    oid = "b" * 40

    with pytest.raises(ValueError, match="Unexpected non-flush terminator"):
        parse_object_info_size_response(_valid_prefix(oid) + b"0002")


def test_object_info_response_rejects_delimiter_as_terminator():
    oid = "c" * 40

    with pytest.raises(ValueError, match="Unexpected non-flush terminator"):
        parse_object_info_size_response(_valid_prefix(oid) + b"0001")


def test_object_info_response_rejects_trailing_bytes_after_flush():
    oid = "d" * 40
    trailing = pkt_line(f"{'e' * 40} 99\n".encode())

    with pytest.raises(ValueError, match="Trailing data after .* flush packet"):
        parse_object_info_size_response(_valid_prefix(oid) + b"0000" + trailing)


def test_object_info_response_accepts_exact_flush_terminated_envelope():
    oid = "f" * 40

    assert parse_object_info_size_response(_valid_prefix(oid, 23) + b"0000") == (
        ObjectSizeInfo(oid, 23),
    )
