from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import build_fetch_request, parse_fetch_response
from pygit.remote import build_pack, pkt_line


def _ack_response(oid: str) -> bytes:
    return pkt_line(b"acknowledgments\n") + pkt_line(f"ACK {oid}\n".encode())


def test_fetch_response_requires_flush_packet():
    oid = "a" * 40

    with pytest.raises(ValueError, match="did not end with flush packet"):
        parse_fetch_response(_ack_response(oid))


def test_fetch_response_rejects_response_end_packet():
    oid = "b" * 40

    with pytest.raises(ValueError, match="Unexpected response-end-pkt"):
        parse_fetch_response(_ack_response(oid) + b"0002")


def test_fetch_response_rejects_trailing_bytes_after_flush():
    oid = "c" * 40
    trailing = pkt_line(b"acknowledgments\n")

    with pytest.raises(ValueError, match="Trailing data after .* flush packet"):
        parse_fetch_response(_ack_response(oid) + b"0000" + trailing)


def test_fetch_response_accepts_exact_flush_terminated_acknowledgments():
    oid = "d" * 40
    parsed = parse_fetch_response(_ack_response(oid) + b"0000")

    assert parsed.acknowledgments == (oid,)
    assert parsed.pack is None


def test_fetch_response_accepts_exact_flush_terminated_packfile():
    pack = build_pack([])
    body = pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack) + b"0000"

    parsed = parse_fetch_response(body)

    assert parsed.pack == pack


def test_native_git_protocol_v2_fetch_is_flush_terminated(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", str(repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "f").write_bytes(b"phase300 fetch framing\n")
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
    request = build_fetch_request(capabilities, [oid])
    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout

    assert response.endswith(b"0000")
    assert not response.endswith(b"0002")
    parsed = parse_fetch_response(response)
    assert parsed.pack is not None
    assert parsed.pack.startswith(b"PACK")
