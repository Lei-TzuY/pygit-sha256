from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    parse_capability_advertisement,
)
from pygit.protocol_v2_object_info import (
    ObjectSizeInfo,
    SmartHttpV2ObjectInfoClient,
    build_object_info_size_request,
    parse_object_info_size_response,
)
from pygit.remote import pkt_line


def _caps(*, object_info=True, server_option=False):
    values = {
        "agent": "git/2.55.0",
        "ls-refs": "unborn",
        "fetch": "shallow filter",
        "object-format": "sha1",
    }
    if object_info:
        values["object-info"] = None
    if server_option:
        values["server-option"] = None
    return ProtocolV2Capabilities(values)


def test_build_object_info_size_request_is_deterministic_and_capability_gated():
    oid_a = "a" * 40
    oid_b = "b" * 40
    body = build_object_info_size_request(
        _caps(server_option=True),
        [oid_b, oid_a, oid_b],
        server_options=["trace=1"],
    )

    assert body.startswith(pkt_line(b"command=object-info\n"))
    assert pkt_line(b"agent=pygit/0.1\n") in body
    assert pkt_line(b"server-option=trace=1\n") in body
    assert b"0001" in body
    assert pkt_line(b"size\n") in body
    assert body.count(pkt_line(f"oid {oid_a}\n".encode())) == 1
    assert body.count(pkt_line(f"oid {oid_b}\n".encode())) == 1
    assert body.index(pkt_line(f"oid {oid_a}\n".encode())) < body.index(
        pkt_line(f"oid {oid_b}\n".encode())
    )
    assert body.endswith(b"0000")


def test_build_object_info_size_request_rejects_missing_capability_and_bad_oids():
    with pytest.raises(RuntimeError, match="does not advertise object-info"):
        build_object_info_size_request(_caps(object_info=False), ["a" * 40])

    with pytest.raises(ValueError, match="at least one object id"):
        build_object_info_size_request(_caps(), [])

    with pytest.raises(ValueError, match="40-hex"):
        build_object_info_size_request(_caps(), ["not-an-oid"])


def test_parse_object_info_size_response_preserves_unknown_oids():
    known = "a" * 40
    missing = "b" * 40
    body = (
        pkt_line(b"size\n")
        + pkt_line(f"{known} 123\n".encode())
        + pkt_line(f"{missing} \n".encode())
        + b"0000"
    )

    assert parse_object_info_size_response(body) == (
        ObjectSizeInfo(known, 123),
        ObjectSizeInfo(missing, None),
    )
    assert parse_object_info_size_response(body)[0].exists is True
    assert parse_object_info_size_response(body)[1].exists is False


def test_parse_object_info_size_response_rejects_malformed_protocol():
    oid = "a" * 40

    with pytest.raises(ValueError, match="did not begin with size"):
        parse_object_info_size_response(pkt_line(f"{oid} 1\n".encode()) + b"0000")

    with pytest.raises(ValueError, match="Duplicate size attribute"):
        parse_object_info_size_response(
            pkt_line(b"size\n") + pkt_line(b"size\n") + b"0000"
        )

    with pytest.raises(ValueError, match="Malformed protocol-v2 object-info size"):
        parse_object_info_size_response(
            pkt_line(b"size\n") + pkt_line(f"{oid} nope\n".encode()) + b"0000"
        )

    with pytest.raises(ValueError, match="Duplicate protocol-v2 object-info result"):
        parse_object_info_size_response(
            pkt_line(b"size\n")
            + pkt_line(f"{oid} 1\n".encode())
            + pkt_line(f"{oid} 1\n".encode())
            + b"0000"
        )


def test_object_info_http_exchange_queries_sizes_without_pack_fetch(monkeypatch):
    known = "a" * 40
    missing = "b" * 40
    capabilities = (
        pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/2.55.0\n")
        + pkt_line(b"object-format=sha1\n")
        + pkt_line(b"object-info\n")
        + b"0000"
    )
    object_info = (
        pkt_line(b"size\n")
        + pkt_line(f"{known} 42\n".encode())
        + pkt_line(f"{missing} \n".encode())
        + b"0000"
    )
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    responses = iter([Response(capabilities), Response(object_info)])

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = SmartHttpV2ObjectInfoClient(
        "https://example.test/repo.git"
    ).query_sizes([missing, known])

    assert result == {known: 42, missing: None}
    assert len(requests) == 2
    assert requests[0].headers["Git-protocol"] == "version=2"
    assert requests[1].full_url.endswith("/git-upload-pack")
    assert pkt_line(b"command=object-info\n") in requests[1].data
    assert pkt_line(b"size\n") in requests[1].data
    assert b"command=fetch" not in requests[1].data


def test_object_info_http_exchange_distinguishes_v0_from_v2_without_capability(
    monkeypatch,
):
    head = "a" * 40
    v0 = (
        pkt_line(b"# service=git-upload-pack\n")
        + b"0000"
        + pkt_line(f"{head} HEAD\x00multi_ack\n".encode())
        + b"0000"
    )

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: Response(v0),
    )
    assert SmartHttpV2ObjectInfoClient("https://example.test/repo.git").query_sizes(
        [head]
    ) is None

    v2_without_object_info = (
        pkt_line(b"version 2\n")
        + pkt_line(b"object-format=sha1\n")
        + pkt_line(b"ls-refs\n")
        + b"0000"
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: Response(v2_without_object_info),
    )
    with pytest.raises(RuntimeError, match="does not advertise object-info"):
        SmartHttpV2ObjectInfoClient("https://example.test/repo.git").query_sizes([head])


def test_object_info_client_rejects_response_oid_mismatch(monkeypatch):
    requested = "a" * 40
    other = "b" * 40
    capabilities = (
        pkt_line(b"version 2\n")
        + pkt_line(b"object-format=sha1\n")
        + pkt_line(b"object-info\n")
        + b"0000"
    )
    response = pkt_line(b"size\n") + pkt_line(f"{other} 1\n".encode()) + b"0000"

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    responses = iter([Response(capabilities), Response(response)])
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: next(responses),
    )

    with pytest.raises(ValueError, match="did not match requested OIDs"):
        SmartHttpV2ObjectInfoClient("https://example.test/repo.git").query_sizes(
            [requested]
        )


def test_native_git_object_info_size_round_trip(tmp_path):
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
    (repo / "f").write_bytes(b"object-info payload\n")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "commit", "-m", "one"],
        check=True,
        stdout=subprocess.PIPE,
    )
    oid = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    expected_size = int(
        subprocess.check_output(
            [git, "-C", str(repo), "cat-file", "-s", oid], text=True
        ).strip()
    )

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    command = [
        git,
        "-c",
        "transfer.advertiseObjectInfo=true",
        "upload-pack",
        "--stateless-rpc",
    ]
    advertised = subprocess.run(
        [*command, "--advertise-refs", str(repo)],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    capabilities = parse_capability_advertisement(advertised)
    if capabilities is None or not capabilities.supports("object-info"):
        pytest.skip("native git does not advertise protocol-v2 object-info")

    request = build_object_info_size_request(capabilities, [oid])
    response = subprocess.run(
        [*command, str(repo)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout

    assert parse_object_info_size_response(response) == (
        ObjectSizeInfo(oid, expected_size),
    )
