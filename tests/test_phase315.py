from __future__ import annotations

import os
import subprocess

import pytest

from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    build_ls_refs_request,
    parse_capability_advertisement,
)
from pygit.protocol_v2_unborn import (
    SmartHttpV2UnbornQueryClient,
    parse_ls_refs_response_with_unborn,
)
from pygit.remote import pkt_line


OID = "1" * 40
PEELED = "2" * 40


def _caps(*, unborn: bool = True) -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "ls-refs": "unborn" if unborn else None,
            "fetch": "shallow wait-for-done",
            "object-format": "sha1",
        }
    )


def _ls_refs_response(line: bytes) -> bytes:
    return pkt_line(line) + b"0000"


def test_parse_preserves_unborn_head_without_fabricating_oid() -> None:
    result = parse_ls_refs_response_with_unborn(
        _ls_refs_response(b"unborn HEAD symref-target:refs/heads/main\n"),
        _caps(),
    )

    assert result.unborn == frozenset({"HEAD"})
    assert result.advertisement.refs == {}
    assert result.advertisement.symrefs == {"HEAD": "refs/heads/main"}
    assert "0" * 40 not in result.advertisement.refs.values()


def test_parse_accepts_native_no_terminal_lf_for_unborn_record() -> None:
    result = parse_ls_refs_response_with_unborn(
        _ls_refs_response(b"unborn HEAD symref-target:refs/heads/main"),
        _caps(),
    )

    assert result.unborn == frozenset({"HEAD"})


def test_parse_rejects_unborn_when_feature_was_not_advertised() -> None:
    with pytest.raises(ValueError, match="without advertising the unborn feature"):
        parse_ls_refs_response_with_unborn(
            _ls_refs_response(b"unborn HEAD symref-target:refs/heads/main\n"),
            _caps(unborn=False),
        )


def test_parse_rejects_unborn_for_non_head_ref() -> None:
    with pytest.raises(ValueError, match="must describe HEAD"):
        parse_ls_refs_response_with_unborn(
            _ls_refs_response(
                b"unborn refs/heads/main symref-target:refs/heads/main\n"
            ),
            _caps(),
        )


def test_parse_rejects_unborn_without_symref_target() -> None:
    with pytest.raises(ValueError, match="missing symref-target"):
        parse_ls_refs_response_with_unborn(
            _ls_refs_response(b"unborn HEAD\n"),
            _caps(),
        )


def test_parse_rejects_unborn_with_peeled_metadata() -> None:
    with pytest.raises(ValueError, match="cannot carry peeled"):
        parse_ls_refs_response_with_unborn(
            _ls_refs_response(
                f"unborn HEAD symref-target:refs/heads/main peeled:{PEELED}\n".encode()
            ),
            _caps(),
        )


def test_regular_ref_keeps_unborn_set_empty() -> None:
    result = parse_ls_refs_response_with_unborn(
        _ls_refs_response(f"{OID} refs/heads/main\n".encode()),
        _caps(),
    )

    assert result.unborn == frozenset()
    assert result.advertisement.refs == {"refs/heads/main": OID}


class _Response:
    def __init__(self, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_smart_http_client_requests_and_preserves_unborn(monkeypatch) -> None:
    advertisement = (
        pkt_line(b"# service=git-upload-pack\n")
        + b"0000"
        + pkt_line(b"version 2\n")
        + pkt_line(b"agent=git/test\n")
        + pkt_line(b"ls-refs=unborn\n")
        + pkt_line(b"object-format=sha1\n")
        + b"0000"
    )
    ls_refs = _ls_refs_response(b"unborn HEAD symref-target:refs/heads/main\n")
    responses = iter(
        [
            _Response(
                advertisement,
                "application/x-git-upload-pack-advertisement",
            ),
            _Response(ls_refs, "application/x-git-upload-pack-result"),
        ]
    )
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("pygit.protocol_v2_unborn.urllib.request.urlopen", fake_urlopen)

    result = SmartHttpV2UnbornQueryClient("https://example.invalid/repo").discover_refs_with_unborn()

    assert result is not None
    assert result.unborn == frozenset({"HEAD"})
    assert result.advertisement.refs == {}
    assert result.advertisement.symrefs == {"HEAD": "refs/heads/main"}
    assert len(requests) == 2
    assert requests[1].data is not None
    assert pkt_line(b"unborn\n") in requests[1].data


def _native_upload_pack(repo, request: bytes | None, *, advertise: bool = False) -> bytes:
    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    command = ["git", "upload-pack", "--stateless-rpc"]
    if advertise:
        command.append("--advertise-refs")
    command.append(str(repo))
    completed = subprocess.run(
        command,
        input=request,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=True,
    )
    return completed.stdout


def test_native_git_empty_repo_round_trip_preserves_actual_unborn_set(tmp_path) -> None:
    repo = tmp_path / "empty.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(repo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    capabilities = parse_capability_advertisement(
        _native_upload_pack(repo, None, advertise=True)
    )
    assert capabilities is not None
    assert capabilities.feature("ls-refs", "unborn")

    response = _native_upload_pack(repo, build_ls_refs_request(capabilities))
    result = parse_ls_refs_response_with_unborn(response, capabilities)

    assert result.unborn == frozenset({"HEAD"})
    assert result.advertisement.refs == {}
    assert result.advertisement.symrefs == {"HEAD": "refs/heads/main"}

    filtered_response = _native_upload_pack(
        repo,
        build_ls_refs_request(
            capabilities,
            prefixes=("refs/heads/feature",),
        ),
    )
    filtered = parse_ls_refs_response_with_unborn(filtered_response, capabilities)
    assert filtered.unborn == frozenset()
    assert filtered.advertisement.refs == {}
    assert filtered.advertisement.symrefs == {}
