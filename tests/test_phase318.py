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
from pygit.protocol_v2_packfile_uris import (
    PackfileUriDescriptor,
    SmartHttpV2PackfileUriClient,
    build_packfile_uri_fetch_request,
    normalize_packfile_uri_protocols,
    parse_fetch_response_with_packfile_uris,
    validate_packfile_uri_response,
)
from pygit.remote import PackParser, build_pack, pkt_line


def _caps(*, uris: bool = True, sideband_all: bool = True) -> ProtocolV2Capabilities:
    features = ["shallow", "wait-for-done"]
    if sideband_all:
        features.append("sideband-all")
    if uris:
        features.append("packfile-uris")
    return ProtocolV2Capabilities(
        {
            "agent": "git/2.55.0",
            "ls-refs": "unborn",
            "fetch": " ".join(features),
            "object-format": "sha1",
        }
    )


def _ordinary_uri_response(pack_hash: str, uri: bytes, pack: bytes) -> bytes:
    return (
        pkt_line(b"packfile-uris\n")
        + pkt_line(pack_hash.encode() + b" " + uri + b"\n")
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x01" + pack)
        + b"0000"
    )


def _sideband_all_uri_response(pack_hash: str, uri: bytes, pack: bytes) -> bytes:
    return (
        pkt_line(b"\x01packfile-uris\n")
        + pkt_line(b"\x01" + pack_hash.encode() + b" " + uri + b"\n")
        + b"0001"
        + pkt_line(b"\x01packfile\n")
        + pkt_line(b"\x01" + pack)
        + b"0000"
    )


def test_normalize_packfile_uri_protocols_preserves_order_and_casefolds():
    assert normalize_packfile_uri_protocols(["HTTPS", "http"]) == ("https", "http")


@pytest.mark.parametrize("protocols", [[], ["ssh"], ["https", "HTTPS"]])
def test_normalize_packfile_uri_protocols_rejects_invalid_sets(protocols):
    with pytest.raises(ValueError):
        normalize_packfile_uri_protocols(protocols)


def test_normalize_packfile_uri_protocols_requires_strings():
    with pytest.raises(TypeError, match="must be a string"):
        normalize_packfile_uri_protocols([None])  # type: ignore[list-item]


def test_build_packfile_uri_request_requires_advertised_capability():
    with pytest.raises(RuntimeError, match="does not advertise packfile-uris"):
        build_packfile_uri_fetch_request(
            _caps(uris=False),
            ["a" * 40],
            ["https"],
        )


def test_build_packfile_uri_request_emits_one_uri_line_and_native_sideband_all():
    body = build_packfile_uri_fetch_request(
        _caps(),
        ["a" * 40],
        ["https", "http"],
        haves=["b" * 40],
    )

    want = pkt_line(b"want " + b"a" * 40 + b"\n")
    have = pkt_line(b"have " + b"b" * 40 + b"\n")
    sideband = pkt_line(b"sideband-all\n")
    uris = pkt_line(b"packfile-uris https,http\n")
    done = pkt_line(b"done\n")

    assert body.count(sideband) == 1
    assert body.count(uris) == 1
    assert body.index(want) < body.index(have) < body.index(sideband)
    assert body.index(sideband) < body.index(uris) < body.index(done)
    assert body.endswith(done + b"0000")


def test_build_packfile_uri_request_does_not_invent_sideband_all():
    body = build_packfile_uri_fetch_request(
        _caps(sideband_all=False),
        ["a" * 40],
        ["https"],
    )
    assert pkt_line(b"sideband-all\n") not in body
    assert pkt_line(b"packfile-uris https\n") in body


def test_build_packfile_uri_request_keeps_existing_oid_and_shallow_validation():
    with pytest.raises(ValueError, match="40-hex"):
        build_packfile_uri_fetch_request(_caps(), ["bad"], ["https"])

    with pytest.raises(RuntimeError, match="does not advertise shallow"):
        build_packfile_uri_fetch_request(
            ProtocolV2Capabilities(
                {"fetch": "sideband-all packfile-uris", "object-format": "sha1"}
            ),
            ["a" * 40],
            ["https"],
            deepen=1,
        )


def test_parse_unbanded_packfile_uri_section_preserves_raw_uri_bytes():
    pack = build_pack([])
    pack_hash = "a" * 40
    uri = b"https://example.test/packs/blob-%ff.pack"

    parsed = parse_fetch_response_with_packfile_uris(
        _ordinary_uri_response(pack_hash, uri, pack)
    )

    assert parsed.sideband_all is False
    assert parsed.packfile_uris == (PackfileUriDescriptor(pack_hash, uri),)
    assert parsed.fetch.pack == pack


def test_parse_native_sideband_all_uri_section_restores_base_fetch_shape():
    pack = build_pack([])
    parsed = parse_fetch_response_with_packfile_uris(
        _sideband_all_uri_response(
            "b" * 40,
            b"https://example.test/packs/blob.pack",
            pack,
        )
    )

    assert parsed.sideband_all is True
    assert parsed.packfile_uris[0].pack_hash == "b" * 40
    assert parsed.packfile_uris[0].scheme == "https"
    assert parsed.fetch.pack == pack


def test_sideband_all_progress_is_ignored_and_fatal_channel_is_preserved():
    pack = build_pack([])
    response = (
        pkt_line(b"\x02counting objects\n")
        + _sideband_all_uri_response(
            "c" * 40,
            b"https://example.test/p.pack",
            pack,
        )
    )
    parsed = parse_fetch_response_with_packfile_uris(response)
    assert parsed.fetch.pack == pack

    with pytest.raises(RuntimeError, match="remote exploded"):
        parse_fetch_response_with_packfile_uris(
            pkt_line(b"\x03remote exploded\n") + b"0000"
        )


@pytest.mark.parametrize(
    "record",
    [
        b"not-a-hash https://example.test/p.pack\n",
        b"a" * 40 + b"\n",
        b"a" * 40 + b" \n",
        b"a" * 40 + b" https://example.test/bad\x01.pack\n",
    ],
)
def test_packfile_uri_descriptor_rejects_malformed_records(record):
    pack = build_pack([])
    data = (
        pkt_line(b"packfile-uris\n")
        + pkt_line(record)
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x01" + pack)
        + b"0000"
    )
    with pytest.raises(ValueError):
        parse_fetch_response_with_packfile_uris(data)


def test_packfile_uri_section_rejects_duplicate_pack_hashes():
    pack = build_pack([])
    pack_hash = b"d" * 40
    data = (
        pkt_line(b"packfile-uris\n")
        + pkt_line(pack_hash + b" https://example.test/a.pack\n")
        + pkt_line(pack_hash + b" https://example.test/b.pack\n")
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x01" + pack)
        + b"0000"
    )
    with pytest.raises(ValueError, match="Duplicate protocol-v2 packfile URI hash"):
        parse_fetch_response_with_packfile_uris(data)


def test_packfile_uri_section_must_directly_precede_packfile():
    data = (
        pkt_line(b"packfile-uris\n")
        + pkt_line(b"e" * 40 + b" https://example.test/a.pack\n")
        + b"0001"
        + pkt_line(b"wanted-refs\n")
        + pkt_line(b"f" * 40 + b" refs/heads/main\n")
        + b"0001"
        + pkt_line(b"packfile\n")
        + pkt_line(b"\x01" + build_pack([]))
        + b"0000"
    )
    with pytest.raises(ValueError, match="must directly precede packfile"):
        parse_fetch_response_with_packfile_uris(data)


def test_no_packfile_uri_section_remains_a_valid_requested_response():
    pack = build_pack([])
    parsed = parse_fetch_response_with_packfile_uris(
        pkt_line(b"packfile\n") + pkt_line(b"\x01" + pack) + b"0000"
    )
    validate_packfile_uri_response(parsed, ["https"])
    assert parsed.packfile_uris == ()


def test_request_aware_validation_rejects_unrequested_uri_scheme():
    parsed = parse_fetch_response_with_packfile_uris(
        _ordinary_uri_response(
            "f" * 40,
            b"http://example.test/p.pack",
            build_pack([]),
        )
    )
    with pytest.raises(ValueError, match="unrequested protocol: http"):
        validate_packfile_uri_response(parsed, ["https"])


class _Response:
    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


def test_smart_http_packfile_uri_client_exposes_descriptors_without_downloading(monkeypatch):
    oid = "a" * 40
    pack_hash = "b" * 40
    capabilities = _Response(
        pkt_line(b"version 2")
        + pkt_line(b"agent=git/2.55.0")
        + pkt_line(b"ls-refs=unborn")
        + pkt_line(b"fetch=shallow wait-for-done sideband-all packfile-uris")
        + pkt_line(b"object-format=sha1")
        + b"0000",
        _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE,
    )
    refs = _Response(
        pkt_line(f"{oid} refs/heads/main".encode()) + b"0000",
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    fetch = _Response(
        _sideband_all_uri_response(
            pack_hash,
            b"https://cdn.example.test/blob.pack",
            build_pack([]),
        ),
        _UPLOAD_PACK_RESULT_MEDIA_TYPE,
    )
    responses = iter([capabilities, refs, fetch])
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = SmartHttpV2PackfileUriClient(
        "https://example.test/repo.git"
    ).fetch_with_packfile_uris(["https"])

    assert result is not None
    assert result.objects == {}
    assert result.packfile_uris == (
        PackfileUriDescriptor(pack_hash, b"https://cdn.example.test/blob.pack"),
    )
    assert len(requests) == 3
    assert pkt_line(b"packfile-uris https\n") in requests[2].data
    assert pkt_line(b"sideband-all\n") in requests[2].data
    # No fourth HTTP request is made: Phase318 intentionally exposes but does
    # not download an arbitrary external URI.


def test_native_git_packfile_uri_round_trip_exposes_blob_pack(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run(
        [git, "init", "-b", "main", str(repo)],
        check=True,
        stdout=subprocess.PIPE,
    )
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "payload.bin").write_bytes(b"phase318 native packfile uri\n")
    subprocess.run([git, "-C", str(repo), "add", "payload.bin"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "commit", "-m", "one"],
        check=True,
        stdout=subprocess.PIPE,
    )

    head = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    blob = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "HEAD:payload.bin"], text=True
    ).strip()
    external_pack = subprocess.run(
        [git, "-C", str(repo), "pack-objects", "--stdout"],
        input=(blob + "\n").encode(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert external_pack.startswith(b"PACK")
    pack_hash = external_pack[-20:].hex()
    uri = b"https://cdn.example.test/phase318-blob.pack"

    subprocess.run(
        [
            git,
            "-C",
            str(repo),
            "config",
            "--add",
            "uploadpack.blobPackfileUri",
            f"{blob} {pack_hash} {uri.decode()}",
        ],
        check=True,
    )
    subprocess.run(
        [git, "-C", str(repo), "config", "uploadpack.allowSidebandAll", "true"],
        check=True,
    )

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
    assert capabilities.feature("fetch", "packfile-uris")
    assert capabilities.feature("fetch", "sideband-all")

    request = build_packfile_uri_fetch_request(
        capabilities,
        [head],
        ["https"],
    )
    response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed = parse_fetch_response_with_packfile_uris(response)
    validate_packfile_uri_response(parsed, ["https"])

    assert parsed.sideband_all is True
    assert parsed.packfile_uris == (PackfileUriDescriptor(pack_hash, uri),)
    assert parsed.fetch.pack is not None
    inline_objects = PackParser(parsed.fetch.pack).parse()
    assert sorted(obj.type_name for obj in inline_objects.values()) == ["commit", "tree"]
    assert all(obj.type_name != "blob" for obj in inline_objects.values())
