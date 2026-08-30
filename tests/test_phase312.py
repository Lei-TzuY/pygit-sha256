from __future__ import annotations

import os
import shutil
import subprocess
from email.message import Message

import pytest

from pygit.protocol_v2 import (
    ProtocolV2Capabilities,
    _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    parse_capability_advertisement,
)
from pygit.protocol_v2_fetch import parse_fetch_response
from pygit.protocol_v2_filter_fetch import (
    SmartHttpV2FilterFetchClient,
    build_filtered_fetch_request,
    normalize_filter_spec,
)
from pygit.remote import PackParser, build_pack, pkt_line


def _caps(*, filter_feature: bool = True) -> ProtocolV2Capabilities:
    features = "shallow wait-for-done"
    if filter_feature:
        features += " filter"
    return ProtocolV2Capabilities(
        {
            "agent": "git/2.55.0",
            "ls-refs": "unborn",
            "fetch": features,
            "object-format": "sha1",
        }
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("blob:none", "blob:none"),
        ("blob:limit=0", "blob:limit=0"),
        ("blob:limit=1k", "blob:limit=1024"),
        ("blob:limit=2m", "blob:limit=2097152"),
        ("blob:limit=1g", "blob:limit=1073741824"),
        ("tree:2", "tree:2"),
        ("object:type=tree", "object:type=tree"),
        ("combine:tree%3A2+blob%3Anone", "combine:tree%3A2+blob%3Anone"),
        ("future-filter:value", "future-filter:value"),
    ],
)
def test_normalize_filter_spec_preserves_safe_specs_and_expands_scaled_limits(
    source, expected
):
    assert normalize_filter_spec(source) == expected


@pytest.mark.parametrize(
    "filter_spec",
    [
        "",
        "blob:none extra",
        "blob:none\n",
        "blob:none\r",
        "blob:none\x00",
        "blob:limit=1x",
        "blob:limit=-1",
    ],
)
def test_normalize_filter_spec_rejects_unsafe_or_malformed_specs(filter_spec):
    with pytest.raises(ValueError):
        normalize_filter_spec(filter_spec)


def test_normalize_filter_spec_requires_string():
    with pytest.raises(TypeError, match="must be a string"):
        normalize_filter_spec(None)  # type: ignore[arg-type]


def test_build_filtered_fetch_request_requires_advertised_filter():
    with pytest.raises(RuntimeError, match="does not advertise filter"):
        build_filtered_fetch_request(_caps(filter_feature=False), ["a" * 40], "blob:none")


def test_build_filtered_fetch_request_frames_one_filter_before_done():
    want = "a" * 40
    have = "b" * 40

    body = build_filtered_fetch_request(
        _caps(),
        [want],
        "blob:limit=1k",
        haves=[have],
    )

    want_record = pkt_line(f"want {want}\n".encode())
    have_record = pkt_line(f"have {have}\n".encode())
    filter_record = pkt_line(b"filter blob:limit=1024\n")
    done_record = pkt_line(b"done\n")

    assert body.count(filter_record) == 1
    assert want_record in body
    assert have_record in body
    assert body.index(want_record) < body.index(have_record)
    assert body.index(have_record) < body.index(filter_record)
    assert body.index(filter_record) < body.index(done_record)
    assert body.endswith(done_record + b"0000")


def test_build_filtered_fetch_request_keeps_existing_oid_and_shallow_validation():
    with pytest.raises(ValueError, match="40-hex"):
        build_filtered_fetch_request(_caps(), ["not-an-oid"], "blob:none")

    with pytest.raises(RuntimeError, match="does not advertise shallow"):
        build_filtered_fetch_request(
            ProtocolV2Capabilities(
                {"fetch": "filter", "object-format": "sha1"}
            ),
            ["a" * 40],
            "blob:none",
            deepen=1,
        )


class _Response:
    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.read_calls = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


def test_smart_http_filtered_fetch_reuses_strict_discovery_refs_and_pack_parser(
    monkeypatch,
):
    oid = "c" * 40
    capabilities = _Response(
        pkt_line(b"version 2")
        + pkt_line(b"agent=git/2.55.0")
        + pkt_line(b"ls-refs=unborn")
        + pkt_line(b"fetch=shallow wait-for-done filter")
        + pkt_line(b"object-format=sha1")
        + b"0000",
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    refs = _Response(
        pkt_line(f"{oid} refs/heads/main".encode()) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    pack = build_pack([])
    fetch = _Response(
        pkt_line(b"packfile") + pkt_line(b"\x01" + pack) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([capabilities, refs, fetch])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = SmartHttpV2FilterFetchClient(
        "https://example.test/repo.git"
    ).fetch_filtered("blob:none")

    assert result is not None
    assert result.advertisement.refs == {"refs/heads/main": oid}
    assert result.objects == {}
    assert capabilities.read_calls == refs.read_calls == fetch.read_calls == 1
    assert len(requests) == 3
    assert requests[0].data is None
    assert pkt_line(b"command=ls-refs\n") in requests[1].data
    assert pkt_line(b"command=fetch\n") in requests[2].data
    assert pkt_line(b"filter blob:none\n") in requests[2].data
    assert pkt_line(b"done\n") in requests[2].data


def test_native_git_protocol_v2_blob_none_fetch_omits_blob(tmp_path):
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
    subprocess.run(
        [git, "-C", str(repo), "config", "uploadpack.allowFilter", "true"],
        check=True,
    )
    (repo / "payload.txt").write_bytes(b"phase312 native filtered blob\n")
    subprocess.run([git, "-C", str(repo), "add", "payload.txt"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "commit", "-m", "one"],
        check=True,
        stdout=subprocess.PIPE,
    )
    oid = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    env = dict(os.environ)
    env["GIT_PROTOCOL"] = "version=2"
    advertisement = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", "--advertise-refs", str(repo)],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    capabilities = parse_capability_advertisement(advertisement)

    assert capabilities is not None
    assert capabilities.feature("fetch", "filter")

    request = build_filtered_fetch_request(capabilities, [oid], "blob:none")
    response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed = parse_fetch_response(response)

    assert parsed.pack is not None
    objects = PackParser(parsed.pack).parse()
    types = sorted(obj.type_name for obj in objects.values())
    assert types == ["commit", "tree"]
    assert all(obj.type_name != "blob" for obj in objects.values())
