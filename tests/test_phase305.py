from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities, parse_capability_advertisement
from pygit.protocol_v2_fetch import (
    SmartHttpV2FetchClient,
    _validate_fetch_response_for_request,
    build_fetch_request,
    parse_fetch_response,
)
from pygit.remote import build_pack, pkt_line


def _caps(*, wait_for_done=True, shallow=True):
    features = []
    if shallow:
        features.append("shallow")
    if wait_for_done:
        features.append("wait-for-done")
    return ProtocolV2Capabilities(
        {"fetch": " ".join(features), "object-format": "sha1"}
    )


def _ack_section(oid: str):
    return pkt_line(b"acknowledgments\n") + pkt_line(f"ACK {oid}\n".encode())


def _pack_section():
    pack = build_pack([])
    return pack, pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack)


def test_fetch_parser_accepts_acknowledgments_only_response():
    oid = "a" * 40
    parsed = parse_fetch_response(_ack_section(oid) + b"0000")
    assert parsed.acknowledgments == (oid,)
    assert parsed.pack is None


def test_fetch_parser_rejects_missing_final_flush():
    oid = "b" * 40
    with pytest.raises(ValueError, match="did not end with flush packet"):
        parse_fetch_response(_ack_section(oid))


def test_fetch_parser_rejects_response_end_terminator():
    oid = "c" * 40
    with pytest.raises(ValueError, match="response-end-pkt"):
        parse_fetch_response(_ack_section(oid) + b"0002")


def test_fetch_parser_rejects_trailing_data_after_flush():
    oid = "c" * 40
    with pytest.raises(ValueError, match="Trailing data"):
        parse_fetch_response(_ack_section(oid) + b"0000junk")


def test_fetch_parser_rejects_leading_delimiter():
    oid = "c" * 40
    with pytest.raises(ValueError, match="delimiter before"):
        parse_fetch_response(b"0001" + _ack_section(oid) + b"0000")


def test_fetch_parser_rejects_empty_section_before_delimiter():
    body = pkt_line(b"acknowledgments\n") + b"0001" + pkt_line(b"packfile\n") + b"0000"
    with pytest.raises(ValueError, match="contained no result"):
        parse_fetch_response(body)


def test_fetch_parser_rejects_terminal_delimiter():
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

    with pytest.raises(ValueError, match="must contain ready"):
        parse_fetch_response(body)


def test_ready_acknowledgment_allows_packfile_same_response():
    _, pack_section = _pack_section()
    body = (
        pkt_line(b"acknowledgments\n")
        + pkt_line(b"ready\n")
        + b"0001"
        + pack_section
        + b"0000"
    )
    parsed = parse_fetch_response(body)
    assert parsed.ready
    assert parsed.pack is not None


def test_done_request_rejects_acknowledgments():
    oid = "f" * 40
    parsed = parse_fetch_response(_ack_section(oid) + b"0000")
    with pytest.raises(ValueError, match="must omit acknowledgments"):
        _validate_fetch_response_for_request(parsed, done=True)


def test_wait_for_done_rejects_ready():
    parsed = parse_fetch_response(
        pkt_line(b"acknowledgments\n") + pkt_line(b"ready\n") + b"0000"
    )
    with pytest.raises(ValueError, match="must not contain ready"):
        _validate_fetch_response_for_request(parsed, wait_for_done=True)


def test_build_fetch_request_rejects_wait_for_done_and_done():
    with pytest.raises(ValueError, match="cannot be combined with done"):
        build_fetch_request(
            _caps(),
            ["a" * 40],
            done=True,
            wait_for_done=True,
        )


def test_negotiate_preserves_pack_transition_runtime_error(monkeypatch):
    oid = "a" * 40
    pack = build_pack([])
    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(client, "_discover_refs_with_capabilities", lambda caps: None)
    monkeypatch.setattr(client, "_wants", lambda ad: [oid])
    monkeypatch.setattr(
        client,
        "_post_fetch",
        lambda body: parse_fetch_response(
            pkt_line(b"acknowledgments\n")
            + pkt_line(b"ready\n")
            + b"0001"
            + pkt_line(b"packfile\n")
            + pkt_line(b"\x01" + pack)
            + b"0000"
        ),
    )

    with pytest.raises(RuntimeError, match="unexpectedly advanced to pack transfer"):
        client.negotiate(haves=[oid], advertisement=object())


def test_native_git_v2_fetch_and_wait_for_done_round_trip(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", "-b", "main", str(repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "payload.txt"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "commit", "-m", "one"],
        check=True,
        stdout=subprocess.PIPE,
    )
    head = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    advertised = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", "--advertise-refs", str(repo)],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    capabilities = parse_capability_advertisement(advertised)
    assert capabilities is not None

    request = build_fetch_request(capabilities, [head])
    response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed = parse_fetch_response(response)
    _validate_fetch_response_for_request(parsed, done=True)
    assert parsed.pack is not None

    if capabilities.feature("fetch", "wait-for-done"):
        negotiation = build_fetch_request(
            capabilities,
            [head],
            haves=[head],
            done=False,
            wait_for_done=True,
        )
        negotiation_response = subprocess.run(
            [git, "upload-pack", "--stateless-rpc", str(repo)],
            input=negotiation,
            check=True,
            env=env,
            stdout=subprocess.PIPE,
        ).stdout
        negotiated = parse_fetch_response(negotiation_response)
        _validate_fetch_response_for_request(negotiated, wait_for_done=True)
        assert head in negotiated.acknowledgments
