from __future__ import annotations

import os
import shutil
import subprocess

import pytest

import pygit.protocol_v2_bundle_uri as bundle_uri
from pygit.protocol_v2 import ProtocolV2Capabilities, parse_capability_advertisement
from pygit.remote import pkt_line


def _response(*records: bytes) -> bytes:
    return b"".join(pkt_line(record) for record in records) + b"0000"


def test_build_bundle_uri_request_is_argument_free() -> None:
    capabilities = ProtocolV2Capabilities({"bundle-uri": None})
    assert bundle_uri.build_bundle_uri_request(capabilities) == (
        pkt_line(b"command=bundle-uri\n") + b"0001" + b"0000"
    )


def test_build_bundle_uri_request_ignores_future_capability_value() -> None:
    capabilities = ProtocolV2Capabilities({"bundle-uri": "future-extension"})
    request = bundle_uri.build_bundle_uri_request(capabilities)
    assert request == pkt_line(b"command=bundle-uri\n") + b"0001" + b"0000"


def test_build_bundle_uri_request_reuses_server_option_prefix() -> None:
    capabilities = ProtocolV2Capabilities(
        {"bundle-uri": None, "server-option": None}
    )
    request = bundle_uri.build_bundle_uri_request(
        capabilities,
        server_options=("trace=one", "trace=two"),
    )
    assert request == (
        pkt_line(b"command=bundle-uri\n")
        + pkt_line(b"server-option=trace=one\n")
        + pkt_line(b"server-option=trace=two\n")
        + b"0001"
        + b"0000"
    )


def test_build_bundle_uri_request_requires_capability() -> None:
    with pytest.raises(RuntimeError, match="does not advertise bundle-uri"):
        bundle_uri.build_bundle_uri_request(ProtocolV2Capabilities({}))


def test_parse_bundle_uri_response_preserves_documented_metadata() -> None:
    parsed = bundle_uri.parse_bundle_uri_response(
        _response(
            b"bundle.version=1",
            b"bundle.mode=any\n",
            b"bundle.heuristic=creationToken",
            b"bundle.primary.uri=https://bundles.example/repo.bundle",
            b"bundle.primary.filter=blob:none",
            b"bundle.primary.creationToken=42",
            b"bundle.primary.location=tw",
            b"bundle.backup.uri=https://backup.example/repo.bundle",
        )
    )

    assert parsed == bundle_uri.BundleUriList(
        version=1,
        mode="any",
        heuristic="creationToken",
        bundles=(
            bundle_uri.BundleUriEntry(
                bundle_id="backup",
                uri="https://backup.example/repo.bundle",
            ),
            bundle_uri.BundleUriEntry(
                bundle_id="primary",
                uri="https://bundles.example/repo.bundle",
                filter_spec="blob:none",
                creation_token=42,
                location="tw",
            ),
        ),
    )


def test_parse_bundle_uri_response_matches_git_protocol_defaults() -> None:
    parsed = bundle_uri.parse_bundle_uri_response(
        _response(b"bundle.primary.uri=https://bundles.example/repo.bundle")
    )
    assert parsed is not None
    assert parsed.version == 1
    assert parsed.mode == "all"
    assert parsed.heuristic is None


def test_parse_bundle_uri_response_discards_malformed_text_records() -> None:
    parsed = bundle_uri.parse_bundle_uri_response(
        _response(
            b"not-a-pair",
            b"=missing-key",
            b"missing-value=",
            b"bad\xff=value",
            b"embedded\nnewline=value",
            b"bundle.future=value",
            b"bundle.primary.uri=https://bundles.example/repo.bundle",
        )
    )
    assert parsed is not None
    assert [item.bundle_id for item in parsed.bundles] == ["primary"]


def test_parse_bundle_uri_response_gracefully_ignores_unknown_heuristic() -> None:
    parsed = bundle_uri.parse_bundle_uri_response(
        _response(
            b"bundle.heuristic=future",
            b"bundle.primary.uri=https://bundles.example/repo.bundle",
        )
    )
    assert parsed is not None
    assert parsed.heuristic is None


@pytest.mark.parametrize(
    "record",
    [b"bundle.version=2", b"bundle.version=nope", b"bundle.mode=invalid"],
)
def test_parse_bundle_uri_response_gracefully_rejects_unusable_list(record: bytes) -> None:
    assert bundle_uri.parse_bundle_uri_response(
        _response(record, b"bundle.primary.uri=https://bundles.example/repo.bundle")
    ) is None


def test_parse_bundle_uri_response_duplicate_uri_is_unusable() -> None:
    assert bundle_uri.parse_bundle_uri_response(
        _response(
            b"bundle.primary.uri=https://one.example/repo.bundle",
            b"bundle.primary.uri=https://two.example/repo.bundle",
        )
    ) is None


def test_parse_bundle_uri_response_named_bundle_requires_uri() -> None:
    assert bundle_uri.parse_bundle_uri_response(
        _response(b"bundle.primary.creationToken=42")
    ) is None


@pytest.mark.parametrize(
    "value",
    ["-1", "12x", str(1 << 64)],
)
def test_parse_bundle_uri_response_ignores_invalid_creation_token(value: str) -> None:
    parsed = bundle_uri.parse_bundle_uri_response(
        _response(
            b"bundle.heuristic=creationToken",
            b"bundle.primary.uri=https://bundles.example/repo.bundle",
            f"bundle.primary.creationToken={value}".encode(),
        )
    )
    assert parsed is not None
    assert parsed.bundles[0].creation_token is None


def test_parse_bundle_uri_response_rejects_missing_flush() -> None:
    with pytest.raises(ValueError, match="did not end with flush"):
        bundle_uri.parse_bundle_uri_response(
            pkt_line(b"bundle.version=1")
        )


def test_parse_bundle_uri_response_rejects_response_end() -> None:
    with pytest.raises(ValueError, match="Unexpected non-flush terminator"):
        bundle_uri.parse_bundle_uri_response(b"0002")


def test_parse_bundle_uri_response_rejects_delimiter() -> None:
    with pytest.raises(ValueError, match="Unexpected non-flush terminator"):
        bundle_uri.parse_bundle_uri_response(b"0001")


def test_parse_bundle_uri_response_rejects_trailing_bytes() -> None:
    with pytest.raises(ValueError, match="Trailing data"):
        bundle_uri.parse_bundle_uri_response(b"0000junk")


def test_parse_bundle_uri_response_rejects_truncated_pkt_line() -> None:
    with pytest.raises(ValueError, match="Truncated protocol-v2 pkt-line"):
        bundle_uri.parse_bundle_uri_response(b"0010short")


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str, *, allow_read: bool = True):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.allow_read = allow_read
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        if not self.allow_read:
            raise AssertionError("response body must not be read")
        self.read_count += 1
        return self._body


def _capability_advertisement(*records: bytes) -> bytes:
    return pkt_line(b"version 2\n") + b"".join(pkt_line(r) for r in records) + b"0000"


def test_smart_http_bundle_uri_client_stops_before_external_download(monkeypatch) -> None:
    advertise = _FakeResponse(
        _capability_advertisement(
            b"agent=git/2.55.0\n",
            b"bundle-uri=future\n",
            b"object-format=sha1\n",
        ),
        "application/x-git-upload-pack-advertisement",
    )
    bundle_response = _FakeResponse(
        _response(
            b"bundle.version=1",
            b"bundle.mode=all",
            b"bundle.primary.uri=https://cdn.example/repo.bundle",
        ),
        "application/x-git-upload-pack-result",
    )
    responses = iter((advertise, bundle_response))
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return next(responses)

    monkeypatch.setattr(bundle_uri.urllib.request, "urlopen", fake_urlopen)
    client = bundle_uri.SmartHttpV2BundleUriClient("https://git.example/repo.git")
    result = client.discover_bundle_uris()

    assert result is not None
    assert result.bundles[0].uri == "https://cdn.example/repo.bundle"
    assert len(requests) == 2
    assert requests[0][0].full_url.endswith("/info/refs?service=git-upload-pack")
    assert requests[1][0].full_url == "https://git.example/repo.git/git-upload-pack"
    assert b"command=bundle-uri\n" in requests[1][0].data
    assert b"https://cdn.example/repo.bundle" not in requests[1][0].data


def test_smart_http_bundle_uri_client_skips_post_without_capability(monkeypatch) -> None:
    advertise = _FakeResponse(
        _capability_advertisement(
            b"agent=git/2.55.0\n",
            b"object-format=sha1\n",
        ),
        "application/x-git-upload-pack-advertisement",
    )
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return advertise

    monkeypatch.setattr(bundle_uri.urllib.request, "urlopen", fake_urlopen)
    client = bundle_uri.SmartHttpV2BundleUriClient("https://git.example/repo.git")
    assert client.discover_bundle_uris() is None
    assert len(requests) == 1


def test_smart_http_bundle_uri_client_checks_mime_before_body(monkeypatch) -> None:
    advertise = _FakeResponse(
        _capability_advertisement(
            b"bundle-uri\n",
            b"object-format=sha1\n",
        ),
        "application/x-git-upload-pack-advertisement",
    )
    bad_response = _FakeResponse(b"ignored", "text/html", allow_read=False)
    responses = iter((advertise, bad_response))

    monkeypatch.setattr(
        bundle_uri.urllib.request,
        "urlopen",
        lambda request, timeout: next(responses),
    )
    client = bundle_uri.SmartHttpV2BundleUriClient("https://git.example/repo.git")
    with pytest.raises(ValueError, match="bundle-uri response Content-Type"):
        client.discover_bundle_uris()
    assert bad_response.read_count == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="native Git is required")
def test_native_git_protocol_v2_bundle_uri_round_trip(tmp_path) -> None:
    bare = tmp_path / "repo.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    config = [
        ("uploadpack.advertiseBundleURIs", "true"),
        ("bundle.version", "1"),
        ("bundle.mode", "all"),
        ("bundle.heuristic", "creationToken"),
        ("bundle.primary.uri", "https://bundles.example/repo.bundle"),
        ("bundle.primary.creationToken", "42"),
        ("bundle.primary.location", "tw"),
    ]
    for key, value in config:
        subprocess.run(
            ["git", "-C", str(bare), "config", key, value],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    advertisement_bytes = subprocess.run(
        ["git", "upload-pack", "--stateless-rpc", "--advertise-refs", str(bare)],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    capabilities = parse_capability_advertisement(advertisement_bytes)
    assert capabilities is not None
    assert capabilities.supports("bundle-uri")

    request = bundle_uri.build_bundle_uri_request(capabilities)
    result_bytes = subprocess.run(
        ["git", "upload-pack", "--stateless-rpc", str(bare)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    parsed = bundle_uri.parse_bundle_uri_response(result_bytes)
    assert parsed is not None
    assert parsed.version == 1
    assert parsed.mode == "all"
    assert parsed.heuristic == "creationToken"
    assert parsed.bundles == (
        bundle_uri.BundleUriEntry(
            bundle_id="primary",
            uri="https://bundles.example/repo.bundle",
            creation_token=42,
            location="tw",
        ),
    )
    assert result_bytes.endswith(b"0000")

    bad_request = (
        pkt_line(b"command=bundle-uri\n")
        + b"0001"
        + pkt_line(b"unexpected\n")
        + b"0000"
    )
    rejected = subprocess.run(
        ["git", "upload-pack", "--stateless-rpc", str(bare)],
        input=bad_request,
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert rejected.returncode != 0
    assert b"bundle-uri: unexpected argument" in rejected.stderr
