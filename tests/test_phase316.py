from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities, parse_capability_advertisement
from pygit.protocol_v2_fetch import ProtocolV2FetchResponse, parse_fetch_response
from pygit.protocol_v2_ref_filter_shallow import (
    SmartHttpV2RefFilteredShallowClient,
    build_ref_filtered_shallow_request,
)
from pygit.protocol_v2_ref_in_want import validate_ref_in_want_response
from pygit.protocol_v2_shallow_cutoff import validate_shallow_response_for_request
from pygit.remote import PackParser, build_pack, pkt_line


def _caps(*, ref_in_want=True, shallow=True, filter_feature=True):
    features = ["wait-for-done"]
    if shallow:
        features.append("shallow")
    if filter_feature:
        features.append("filter")
    if ref_in_want:
        features.append("ref-in-want")
    return ProtocolV2Capabilities(
        {"fetch": " ".join(features), "object-format": "sha1"}
    )


def _response(*, wanted_refs=None, shallow=(), unshallow=(), pack=None):
    return ProtocolV2FetchResponse(
        acknowledgments=(),
        ready=False,
        nak=False,
        shallow=tuple(shallow),
        unshallow=tuple(unshallow),
        wanted_refs=dict(wanted_refs or {}),
        pack=pack,
    )


def test_direct_combined_request_orders_all_features_before_done():
    have = "a" * 40
    existing_shallow = "b" * 40
    body = build_ref_filtered_shallow_request(
        _caps(),
        ["HEAD", "refs/heads/main"],
        "blob:limit=1k",
        haves=[have],
        shallow=[existing_shallow],
        deepen_since=1704067200,
        deepen_not=["old", "refs/heads/base"],
    )

    records = [
        pkt_line(f"shallow {existing_shallow}\n".encode()),
        pkt_line(b"deepen-since 1704067200\n"),
        pkt_line(b"deepen-not old\n"),
        pkt_line(b"deepen-not refs/heads/base\n"),
        pkt_line(b"want-ref HEAD\n"),
        pkt_line(b"want-ref refs/heads/main\n"),
        pkt_line(f"have {have}\n".encode()),
        pkt_line(b"filter blob:limit=1024\n"),
        pkt_line(b"done\n"),
    ]
    positions = [body.index(record) for record in records]
    assert positions == sorted(positions)
    assert body.endswith(pkt_line(b"done\n") + b"0000")


def test_direct_combined_request_requires_all_three_features():
    with pytest.raises(RuntimeError, match="does not advertise ref-in-want"):
        build_ref_filtered_shallow_request(
            _caps(ref_in_want=False),
            ["refs/heads/main"],
            "blob:none",
            deepen_since=0,
        )
    with pytest.raises(RuntimeError, match="does not advertise shallow"):
        build_ref_filtered_shallow_request(
            _caps(shallow=False),
            ["refs/heads/main"],
            "blob:none",
            deepen_since=0,
        )
    with pytest.raises(RuntimeError, match="does not advertise filter"):
        build_ref_filtered_shallow_request(
            _caps(filter_feature=False),
            ["refs/heads/main"],
            "blob:none",
            deepen_since=0,
        )


def test_direct_combined_request_preserves_component_validation():
    with pytest.raises(ValueError, match="duplicate protocol-v2 want-ref"):
        build_ref_filtered_shallow_request(
            _caps(),
            ["refs/heads/main", "refs/heads/main"],
            "blob:none",
            deepen_since=0,
        )
    with pytest.raises(ValueError, match="non-negative integer timestamp"):
        build_ref_filtered_shallow_request(
            _caps(),
            ["refs/heads/main"],
            "blob:none",
            deepen_since=-1,
        )
    with pytest.raises(ValueError, match="malformed protocol-v2 blob:limit"):
        build_ref_filtered_shallow_request(
            _caps(),
            ["refs/heads/main"],
            "blob:limit=1x",
            deepen_since=0,
        )
    with pytest.raises(ValueError, match="40-hex"):
        build_ref_filtered_shallow_request(
            _caps(),
            ["refs/heads/main"],
            "blob:none",
            deepen_since=0,
            haves=["not-an-oid"],
        )


def test_direct_combined_request_requires_a_cutoff():
    with pytest.raises(ValueError, match="requires deepen-since and/or deepen-not"):
        build_ref_filtered_shallow_request(
            _caps(),
            ["refs/heads/main"],
            "blob:none",
        )


def test_direct_combined_client_skips_ls_refs_and_applies_both_response_guards(monkeypatch):
    oid = "c" * 40
    declared = "d" * 40
    pack = build_pack([])
    client = SmartHttpV2RefFilteredShallowClient("https://example.test/repo.git")
    seen = {}

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(
        client,
        "_discover_refs_with_capabilities",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("direct ref-in-want fetch must not issue ls-refs")
        ),
    )

    def fake_post(body):
        seen["body"] = body
        return _response(
            wanted_refs={"refs/heads/main": oid},
            shallow=[oid],
            pack=pack,
        )

    monkeypatch.setattr(client, "_post_fetch", fake_post)

    result = client.fetch_refs_filtered_shallow(
        ["refs/heads/main"],
        "blob:none",
        deepen_since=0,
        shallow=[declared],
    )

    assert result is not None
    assert result.advertisement.refs == {"refs/heads/main": oid}
    assert result.shallow == (oid,)
    assert result.objects == {}
    assert pkt_line(b"command=ls-refs\n") not in seen["body"]
    assert pkt_line(b"want-ref refs/heads/main\n") in seen["body"]
    assert pkt_line(b"filter blob:none\n") in seen["body"]


def test_direct_combined_client_rejects_unexpected_wanted_ref(monkeypatch):
    client = SmartHttpV2RefFilteredShallowClient("https://example.test/repo.git")
    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(
        client,
        "_post_fetch",
        lambda body: _response(
            wanted_refs={
                "refs/heads/main": "e" * 40,
                "refs/heads/other": "f" * 40,
            },
            pack=build_pack([]),
        ),
    )

    with pytest.raises(ValueError, match="unrequested refs"):
        client.fetch_refs_filtered_shallow(
            ["refs/heads/main"],
            "blob:none",
            deepen_since=0,
        )


def test_native_git_direct_ref_filtered_shallow_round_trip(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", "-b", "main", str(repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "uploadpack.allowFilter", "true"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "uploadpack.allowRefInWant", "true"], check=True)

    for day, message in ((1, "one"), (2, "two"), (3, "three")):
        (repo / "payload.txt").write_text(message + "\n", encoding="utf-8")
        subprocess.run([git, "-C", str(repo), "add", "payload.txt"], check=True)
        env = dict(os.environ)
        stamp = f"2024-01-0{day}T00:00:00Z"
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
        subprocess.run(
            [git, "-C", str(repo), "commit", "-m", message],
            check=True,
            env=env,
            stdout=subprocess.PIPE,
        )

    subprocess.run([git, "-C", str(repo), "branch", "old", "HEAD~1"], check=True)
    head = subprocess.check_output(
        [git, "-C", str(repo), "rev-parse", "refs/heads/main"], text=True
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
    assert capabilities.feature("fetch", "filter")
    assert capabilities.feature("fetch", "shallow")

    request = build_ref_filtered_shallow_request(
        capabilities,
        ["refs/heads/main"],
        "blob:none",
        deepen_since=1704067200,
        deepen_not=["refs/heads/old"],
    )
    response = subprocess.run(
        [git, "upload-pack", "--stateless-rpc", str(repo)],
        input=request,
        check=True,
        env=env,
        stdout=subprocess.PIPE,
    ).stdout
    parsed = parse_fetch_response(response)
    validate_ref_in_want_response(parsed, ["refs/heads/main"])
    validate_shallow_response_for_request(parsed)

    assert parsed.wanted_refs == {"refs/heads/main": head}
    assert head in parsed.shallow
    assert parsed.pack is not None
    objects = PackParser(parsed.pack).parse()
    types = sorted(obj.type_name for obj in objects.values())
    assert types == ["commit", "tree"]
    assert all(obj.type_name != "blob" for obj in objects.values())
