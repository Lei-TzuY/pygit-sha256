from __future__ import annotations

import os
import subprocess

import pytest

from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    parse_capability_advertisement,
    parse_ls_refs_response,
)
from pygit.remote import pkt_line


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "agent": "git/2.55.0",
            "ls-refs": "unborn",
            "object-format": "sha1",
        }
    )


def _advertisement(*records: bytes, version: bytes = b"version 2\n") -> bytes:
    return pkt_line(version) + b"".join(pkt_line(record) for record in records) + b"0000"


def test_capability_parser_accepts_missing_terminal_lf_and_unknown_valid_key():
    parsed = parse_capability_advertisement(
        _advertisement(
            b"object-format=sha1",
            b"future_cap-1=value/with:punctuation?ok",
            version=b"version 2",
        )
    )

    assert parsed is not None
    assert parsed.value("object-format") == "sha1"
    assert parsed.value("future_cap-1") == "value/with:punctuation?ok"


def test_capability_parser_accepts_extended_printable_agent_value():
    agent = b'phase303~agent|quote"slash\\tick`'
    parsed = parse_capability_advertisement(
        _advertisement(
            b"agent=" + agent + b"\n",
            b"object-format=sha1\n",
        )
    )

    assert parsed is not None
    assert parsed.value("agent") == agent.decode("ascii")


@pytest.mark.parametrize(
    "record, message",
    [
        (b"bad.key=value\n", "capability name"),
        (b"bad key=value\n", "capability name"),
        (b"future=\n", "Empty protocol-v2 capability value"),
        (b"future=value|pipe\n", "capability value"),
        (b"future=value\tbad\n", "capability value"),
        (b"agent=contains space\n", "agent capability value"),
        (b"future=value\n\n", "Unexpected LF"),
        (b"future=value\nother\n", "Unexpected LF"),
    ],
)
def test_capability_parser_rejects_malformed_records(record, message):
    with pytest.raises(ValueError, match=message):
        parse_capability_advertisement(
            _advertisement(record, b"object-format=sha1\n")
        )


def test_version_record_accepts_missing_lf_but_rejects_extra_lf():
    parsed = parse_capability_advertisement(
        _advertisement(b"object-format=sha1\n", version=b"version 2")
    )
    assert parsed is not None

    with pytest.raises(ValueError, match="Unexpected LF"):
        parse_capability_advertisement(
            _advertisement(b"object-format=sha1\n", version=b"version 2\n\n")
        )


def test_ls_refs_accepts_missing_lf_and_preserves_unknown_attribute_compatibility():
    oid = "a" * 40
    parsed = parse_ls_refs_response(
        pkt_line(
            f"{oid} refs/heads/main future-attribute:value".encode()
        )
        + b"0000",
        _caps(),
    )

    assert parsed.refs["refs/heads/main"] == oid


@pytest.mark.parametrize(
    "payload, message",
    [
        (lambda oid: f"{oid}  refs/heads/main\n".encode(), "Malformed"),
        (lambda oid: f"{oid} refs/heads/main\n\n".encode(), "Unexpected LF"),
        (lambda oid: f"{oid} refs/heads/main\x00evil\n".encode(), "NUL"),
        (
            lambda oid: f"{oid} refs/heads/main symref-target:\n".encode(),
            "Empty symref-target",
        ),
        (
            lambda oid: (
                f"{oid} refs/heads/main symref-target:refs/heads/a "
                "symref-target:refs/heads/b\n"
            ).encode(),
            "Duplicate symref-target",
        ),
        (
            lambda oid: (
                f"{oid} refs/tags/v1 peeled:{'b' * 40} peeled:{'c' * 40}\n"
            ).encode(),
            "Duplicate peeled",
        ),
    ],
)
def test_ls_refs_rejects_malformed_record_structure(payload, message):
    oid = "a" * 40
    with pytest.raises(ValueError, match=message):
        parse_ls_refs_response(pkt_line(payload(oid)) + b"0000", _caps())


def test_ls_refs_rejects_duplicate_ref_records_instead_of_overwriting():
    oid_a = "a" * 40
    oid_b = "b" * 40
    body = (
        pkt_line(f"{oid_a} refs/heads/main\n".encode())
        + pkt_line(f"{oid_b} refs/heads/main\n".encode())
        + b"0000"
    )

    with pytest.raises(ValueError, match="Duplicate protocol-v2 ls-refs result"):
        parse_ls_refs_response(body, _caps())


def test_native_git_custom_printable_agent_remains_parseable(tmp_path):
    repo = tmp_path / "native"
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    env["GIT_USER_AGENT"] = 'phase303~agent|quote"slash\\tick`'
    advertised = subprocess.run(
        ["git", "upload-pack", "--stateless-rpc", "--advertise-refs", str(repo)],
        env=env,
        check=True,
        capture_output=True,
    ).stdout

    parsed = parse_capability_advertisement(advertised)
    assert parsed is not None
    assert parsed.value("agent") == env["GIT_USER_AGENT"]
