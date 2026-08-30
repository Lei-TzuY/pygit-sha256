from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities, parse_capability_advertisement
from pygit.protocol_v2_fetch import ProtocolV2FetchResponse, parse_fetch_response
from pygit.protocol_v2_ref_in_want import (
    SmartHttpV2RefInWantClient,
    build_ref_in_want_request,
    validate_ref_in_want_response,
)
from pygit.remote import build_pack, pkt_line


def _caps(value: str = "shallow wait-for-done ref-in-want") -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "fetch": value,
            "object-format": "sha1",
        }
    )


def _response(wanted_refs, *, pack=None) -> ProtocolV2FetchResponse:
    return ProtocolV2FetchResponse(
        acknowledgments=(),
        ready=False,
        nak=False,
        shallow=(),
        unshallow=(),
        wanted_refs=dict(wanted_refs),
        pack=pack,
    )


def test_ref_in_want_requires_advertised_feature():
    with pytest.raises(RuntimeError, match="does not advertise ref-in-want"):
        build_ref_in_want_request(_caps("shallow"), ["refs/heads/main"])


def test_ref_in_want_requires_at_least_one_safe_ref():
    with pytest.raises(ValueError, match="requires at least one ref"):
        build_ref_in_want_request(_caps(), [])

    with pytest.raises(ValueError, match="forbidden character"):
        build_ref_in_want_request(_caps(), ["refs/heads/bad name"])


def test_ref_in_want_rejects_duplicates_instead_of_silently_deduping():
    with pytest.raises(ValueError, match="duplicate protocol-v2 want-ref refs/heads/main"):
        build_ref_in_want_request(
            _caps(),
            ["refs/heads/main", "refs/heads/main"],
        )


def test_ref_in_want_preserves_order_and_allows_native_head_pseudoref():
    body = build_ref_in_want_request(
        _caps(),
        ["HEAD", "refs/heads/main", "refs/tags/v1"],
        haves=["b" * 40, "a" * 40],
    )

    head = pkt_line(b"want-ref HEAD\n")
    main = pkt_line(b"want-ref refs/heads/main\n")
    tag = pkt_line(b"want-ref refs/tags/v1\n")
    assert body.index(head) < body.index(main) < body.index(tag)
    assert body.count(head) == 1
    assert pkt_line(b"have " + b"a" * 40 + b"\n") in body
    assert pkt_line(b"have " + b"b" * 40 + b"\n") in body
    assert body.endswith(pkt_line(b"done\n") + b"0000")


def test_ref_in_want_response_requires_exact_requested_ref_set():
    pack = build_pack([])

    validate_ref_in_want_response(
        _response({"refs/heads/main": "a" * 40}, pack=pack),
        ["refs/heads/main"],
    )

    with pytest.raises(ValueError, match="unrequested refs: refs/heads/other"):
        validate_ref_in_want_response(
            _response(
                {
                    "refs/heads/main": "a" * 40,
                    "refs/heads/other": "b" * 40,
                },
                pack=pack,
            ),
            ["refs/heads/main"],
        )

    with pytest.raises(ValueError, match="omitted requested refs: refs/heads/topic"):
        validate_ref_in_want_response(
            _response({"refs/heads/main": "a" * 40}, pack=pack),
            ["refs/heads/main", "refs/heads/topic"],
        )


def test_ref_in_want_response_still_requires_done_fetch_pack_contract():
    with pytest.raises(ValueError, match="did not contain a packfile"):
        validate_ref_in_want_response(
            _response({"refs/heads/main": "a" * 40}),
            ["refs/heads/main"],
        )


def test_smart_http_ref_in_want_fetch_skips_ls_refs(monkeypatch):
    oid = "c" * 40
    pack = build_pack([])
    client = SmartHttpV2RefInWantClient("https://example.test/repo.git")
    seen = {}

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(
        client,
        "_discover_refs_with_capabilities",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ref-in-want must not issue ls-refs")
        ),
    )

    def fake_post(body):
        seen["body"] = body
        return _response({"refs/heads/main": oid}, pack=pack)

    monkeypatch.setattr(client, "_post_fetch", fake_post)

    result = client.fetch_refs(["refs/heads/main"], haves=["d" * 40])

    assert result is not None
    assert result.advertisement.refs == {"refs/heads/main": oid}
    assert result.objects == {}
    assert pkt_line(b"want-ref refs/heads/main\n") in seen["body"]
    assert pkt_line(b"have " + b"d" * 40 + b"\n") in seen["body"]
    assert pkt_line(b"command=ls-refs\n") not in seen["body"]


def test_native_git_ref_in_want_round_trip(tmp_path):
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
    subprocess.run(
        [git, "-C", str(repo), "config", "uploadpack.allowRefInWant", "true"],
        check=True,
    )
    (repo / "f").write_text("phase311 ref-in-want\n", encoding="utf-8")
    subprocess.run([git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run(
        [git, "-C", str(repo), "commit", "-m", "one"],
        check=True,
        stdout=subprocess.PIPE,
    )
    oid = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "refs/heads/main"],
        text=True,
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
    assert capabilities.feature("fetch", "ref-in-want")

    response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=build_ref_in_want_request(capabilities, ["refs/heads/main"]),
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed = parse_fetch_response(response)
    validate_ref_in_want_response(parsed, ["refs/heads/main"])

    assert parsed.wanted_refs == {"refs/heads/main": oid}
    assert parsed.pack is not None
    assert parsed.pack.startswith(b"PACK")
