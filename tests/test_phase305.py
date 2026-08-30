from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    _validate_fetch_response_for_request,
    build_fetch_request,
    parse_fetch_response,
)
from pygit.remote import build_pack, pkt_line


def _pack_section() -> tuple[bytes, bytes]:
    pack = build_pack([])
    return pack, pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack)


def _ack_section(oid: str, *, ready: bool = False) -> bytes:
    body = pkt_line(b"acknowledgments\n") + pkt_line(f"ACK {oid}\n".encode())
    if ready:
        body += pkt_line(b"ready\n")
    return body


def _response(
    *,
    acknowledgments=(),
    ready=False,
    nak=False,
    pack=None,
) -> ProtocolV2FetchResponse:
    return ProtocolV2FetchResponse(
        acknowledgments=tuple(acknowledgments),
        ready=ready,
        nak=nak,
        shallow=(),
        unshallow=(),
        wanted_refs={},
        pack=pack,
    )


def test_wait_for_done_request_cannot_also_send_done():
    capabilities = ProtocolV2Capabilities(
        {"fetch": "wait-for-done", "object-format": "sha1"}
    )

    with pytest.raises(ValueError, match="wait-for-done cannot be combined with done"):
        build_fetch_request(
            capabilities,
            ["a" * 40],
            wait_for_done=True,
            done=True,
        )


def test_fetch_parser_accepts_missing_terminal_lf_in_text_records():
    oid = "a" * 40
    body = pkt_line(b"acknowledgments") + pkt_line(f"ACK {oid}".encode()) + b"0000"

    parsed = parse_fetch_response(body)

    assert parsed.acknowledgments == (oid,)
    assert parsed.has_acknowledgments


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"acknowledgments\n\n", "Unexpected LF"),
        (b"acknowledgments\r\n", "Unexpected CR"),
        (b"acknowledgments\x00", "Unexpected NUL"),
    ],
)
def test_fetch_parser_rejects_ambiguous_section_header_text(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_fetch_response(pkt_line(payload) + b"0000")


def test_fetch_parser_rejects_delimiter_before_first_section():
    with pytest.raises(ValueError, match="Unexpected delimiter before"):
        parse_fetch_response(b"0001" + pkt_line(b"acknowledgments\n") + b"0000")


def test_fetch_parser_rejects_repeated_delimiter():
    oid = "b" * 40
    body = _ack_section(oid) + b"0001" + b"0001" + pkt_line(b"packfile\n") + b"0000"

    with pytest.raises(ValueError, match="Unexpected delimiter before"):
        parse_fetch_response(body)


def test_fetch_parser_rejects_delimiter_immediately_before_flush():
    oid = "c" * 40
    body = _ack_section(oid) + b"0001" + b"0000"

    with pytest.raises(ValueError, match="ended immediately after delimiter"):
        parse_fetch_response(body)


def test_fetch_parser_rejects_packfile_delimiter():
    pack, section = _pack_section()
    assert pack.startswith(b"PACK")

    with pytest.raises(ValueError, match="delimiter after protocol-v2 packfile"):
        parse_fetch_response(section + b"0001" + pkt_line(b"wanted-refs\n") + b"0000")


@pytest.mark.parametrize(
    "first, second",
    [
        ("shallow-info", "acknowledgments"),
        ("packfile", "wanted-refs"),
    ],
)
def test_fetch_parser_rejects_out_of_order_sections(first, second):
    oid = "d" * 40
    payloads = {
        "acknowledgments": pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {oid}\n".encode()),
        "shallow-info": pkt_line(b"shallow-info\n"),
        "wanted-refs": pkt_line(b"wanted-refs\n"),
        "packfile": _pack_section()[1],
    }
    body = payloads[first] + b"0001" + payloads[second] + b"0000"

    message = "delimiter after protocol-v2 packfile" if first == "packfile" else "Out-of-order"
    with pytest.raises(ValueError, match=message):
        parse_fetch_response(body)


def test_non_acknowledgment_section_requires_packfile():
    body = pkt_line(b"shallow-info\n") + b"0000"

    with pytest.raises(ValueError, match="must end in packfile"):
        parse_fetch_response(body)


def test_ready_acknowledgment_requires_packfile_same_response():
    body = pkt_line(b"acknowledgments\n") + pkt_line(b"ready\n") + b"0000"

    with pytest.raises(ValueError, match="ready acknowledgment requires a packfile"):
        parse_fetch_response(body)


def test_acknowledgments_before_packfile_require_ready():
    oid = "e" * 40
    _, pack_section = _pack_section()
    body = _ack_section(oid) + b"0001" + pack_section + b"0000"

    with pytest.raises(ValueError, match="preceding packfile must contain ready"):
        parse_fetch_response(body)


def test_ack_ready_then_packfile_is_valid_ordered_response():
    oid = "f" * 40
    pack, pack_section = _pack_section()
    body = _ack_section(oid, ready=True) + b"0001" + pack_section + b"0000"

    parsed = parse_fetch_response(body)

    assert parsed.acknowledgments == (oid,)
    assert parsed.ready is True
    assert parsed.pack == pack


def test_nak_cannot_mix_with_ready():
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(b"NAK\n")
        + pkt_line(b"ready\n")
        + b"0000"
    )

    with pytest.raises(ValueError, match="cannot mix NAK with ready"):
        parse_fetch_response(body)


def test_ack_cannot_follow_ready():
    oid = "1" * 40
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(b"ready\n")
        + pkt_line(f"ACK {oid}\n".encode())
        + b"0000"
    )

    with pytest.raises(ValueError, match="ACK cannot appear after ready"):
        parse_fetch_response(body)


def test_duplicate_ack_is_rejected():
    oid = "2" * 40
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(f"ACK {oid}\n".encode())
        + pkt_line(f"ACK {oid}\n".encode())
        + b"0000"
    )

    with pytest.raises(ValueError, match="Duplicate protocol-v2 ACK"):
        parse_fetch_response(body)


def test_empty_acknowledgments_section_is_rejected():
    with pytest.raises(ValueError, match="contained no result"):
        parse_fetch_response(pkt_line(b"acknowledgments\n") + b"0000")


def test_duplicate_and_conflicting_shallow_records_are_rejected():
    oid = "3" * 40
    duplicate = (
        pkt_line(b"shallow-info\n")
        + pkt_line(f"shallow {oid}\n".encode())
        + pkt_line(f"shallow {oid}\n".encode())
        + b"0000"
    )
    conflict = (
        pkt_line(b"shallow-info\n")
        + pkt_line(f"shallow {oid}\n".encode())
        + pkt_line(f"unshallow {oid}\n".encode())
        + b"0000"
    )

    with pytest.raises(ValueError, match="Duplicate protocol-v2 shallow"):
        parse_fetch_response(duplicate)
    with pytest.raises(ValueError, match="Conflicting shallow/unshallow"):
        parse_fetch_response(conflict)


def test_duplicate_wanted_ref_is_rejected_before_overwrite():
    oid_a = "4" * 40
    oid_b = "5" * 40
    body = (
        pkt_line(b"wanted-refs\n")
        + pkt_line(f"{oid_a} refs/heads/main\n".encode())
        + pkt_line(f"{oid_b} refs/heads/main\n".encode())
        + b"0000"
    )

    with pytest.raises(ValueError, match="Duplicate protocol-v2 wanted-ref"):
        parse_fetch_response(body)


def test_done_response_contract_requires_pack_and_forbids_acknowledgments():
    pack = build_pack([])

    with pytest.raises(ValueError, match="must omit acknowledgments"):
        _validate_fetch_response_for_request(
            _response(acknowledgments=("6" * 40,), pack=pack),
            done=True,
        )
    with pytest.raises(ValueError, match="did not contain a packfile"):
        _validate_fetch_response_for_request(_response(), done=True)

    _validate_fetch_response_for_request(_response(pack=pack), done=True)


def test_wait_for_done_response_contract_is_ack_only_and_never_ready():
    oid = "7" * 40
    pack = build_pack([])

    _validate_fetch_response_for_request(
        _response(acknowledgments=(oid,)),
        wait_for_done=True,
    )

    with pytest.raises(ValueError, match="must not contain ready"):
        _validate_fetch_response_for_request(
            _response(ready=True, pack=pack),
            wait_for_done=True,
        )
    with pytest.raises(ValueError, match="must not contain a packfile"):
        _validate_fetch_response_for_request(
            _response(acknowledgments=(oid,), pack=pack),
            wait_for_done=True,
        )
    with pytest.raises(ValueError, match="must contain acknowledgments"):
        _validate_fetch_response_for_request(_response(), wait_for_done=True)


def test_native_git_done_and_wait_for_done_match_state_machine(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", "-b", "main", str(repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "f").write_bytes(b"phase305 fetch state machine\n")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "commit", "-m", "one"],
        check=True,
        stdout=subprocess.PIPE,
    )
    oid = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    capabilities = ProtocolV2Capabilities(
        {
            "fetch": "shallow wait-for-done",
            "object-format": "sha1",
        }
    )
    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"

    done_response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=build_fetch_request(capabilities, [oid], done=True),
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed_done = parse_fetch_response(done_response)
    _validate_fetch_response_for_request(parsed_done, done=True)
    assert parsed_done.pack is not None
    assert not parsed_done.has_acknowledgments

    negotiation_response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=build_fetch_request(
            capabilities,
            [oid],
            haves=[oid],
            done=False,
            wait_for_done=True,
        ),
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed_negotiation = parse_fetch_response(negotiation_response)
    _validate_fetch_response_for_request(parsed_negotiation, wait_for_done=True)
    assert parsed_negotiation.acknowledgments == (oid,)
    assert parsed_negotiation.ready is False
    assert parsed_negotiation.pack is None
