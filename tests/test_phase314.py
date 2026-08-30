from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities, parse_capability_advertisement
from pygit.protocol_v2_fetch import (
    ProtocolV2FetchResponse,
    _validate_fetch_response_for_request,
    parse_fetch_response,
)
from pygit.protocol_v2_filter_shallow import (
    SmartHttpV2FilteredShallowClient,
    build_filtered_shallow_cutoff_fetch_request,
)
from pygit.protocol_v2_shallow_cutoff import validate_shallow_response_for_request
from pygit.remote import Advertisement, PackParser, build_pack, pkt_line


def _caps(*, filter_feature: bool = True, shallow_feature: bool = True):
    features = ["wait-for-done"]
    if shallow_feature:
        features.append("shallow")
    if filter_feature:
        features.append("filter")
    return ProtocolV2Capabilities(
        {"fetch": " ".join(features), "object-format": "sha1"}
    )


def _response(*, shallow=(), unshallow=(), pack=None):
    return ProtocolV2FetchResponse(
        acknowledgments=(),
        ready=False,
        nak=False,
        shallow=tuple(shallow),
        unshallow=tuple(unshallow),
        wanted_refs={},
        pack=pack,
    )


def test_combined_request_orders_cutoffs_before_want_and_filter_before_done():
    want = "a" * 40
    have = "b" * 40
    existing_shallow = "c" * 40

    body = build_filtered_shallow_cutoff_fetch_request(
        _caps(),
        [want],
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
        pkt_line(f"want {want}\n".encode()),
        pkt_line(f"have {have}\n".encode()),
        pkt_line(b"filter blob:limit=1024\n"),
        pkt_line(b"done\n"),
    ]

    positions = [body.index(record) for record in records]
    assert positions == sorted(positions)
    assert body.count(pkt_line(b"filter blob:limit=1024\n")) == 1
    assert body.endswith(pkt_line(b"done\n") + b"0000")


def test_combined_request_requires_both_filter_and_shallow_features():
    with pytest.raises(RuntimeError, match="does not advertise filter"):
        build_filtered_shallow_cutoff_fetch_request(
            _caps(filter_feature=False),
            ["a" * 40],
            "blob:none",
            deepen_since=0,
        )

    with pytest.raises(RuntimeError, match="does not advertise shallow"):
        build_filtered_shallow_cutoff_fetch_request(
            _caps(shallow_feature=False),
            ["a" * 40],
            "blob:none",
            deepen_since=0,
        )


def test_combined_request_preserves_component_validation_contracts():
    with pytest.raises(ValueError, match="non-negative integer timestamp"):
        build_filtered_shallow_cutoff_fetch_request(
            _caps(),
            ["a" * 40],
            "blob:none",
            deepen_since=-1,
        )

    with pytest.raises(ValueError, match="malformed protocol-v2 blob:limit"):
        build_filtered_shallow_cutoff_fetch_request(
            _caps(),
            ["a" * 40],
            "blob:limit=1x",
            deepen_since=0,
        )

    with pytest.raises(ValueError, match="40-hex"):
        build_filtered_shallow_cutoff_fetch_request(
            _caps(),
            ["not-an-oid"],
            "blob:none",
            deepen_since=0,
        )


def test_smart_http_combined_client_keeps_request_aware_unshallow_guard(monkeypatch):
    want = "d" * 40
    declared = "e" * 40
    unexpected = "f" * 40
    advertisement = Advertisement({"refs/heads/main": want}, set(), {})
    client = SmartHttpV2FilteredShallowClient("https://example.test/repo.git")

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())
    monkeypatch.setattr(
        client,
        "_post_fetch",
        lambda body: _response(unshallow=[unexpected], pack=build_pack([])),
    )

    with pytest.raises(ValueError, match="not declared shallow"):
        client.fetch_filtered_shallow(
            "blob:none",
            deepen_since=0,
            advertisement=advertisement,
            shallow=[declared],
        )


def test_smart_http_combined_client_returns_filtered_pack_result(monkeypatch):
    want = "1" * 40
    boundary = "2" * 40
    advertisement = Advertisement({"refs/heads/main": want}, set(), {})
    pack = build_pack([])
    captured = []
    client = SmartHttpV2FilteredShallowClient("https://example.test/repo.git")

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())

    def fake_post(body):
        captured.append(body)
        return _response(shallow=[boundary], pack=pack)

    monkeypatch.setattr(client, "_post_fetch", fake_post)

    result = client.fetch_filtered_shallow(
        "blob:none",
        deepen_since=0,
        deepen_not=["old"],
        advertisement=advertisement,
    )

    assert result is not None
    assert result.objects == {}
    assert result.shallow == (boundary,)
    assert len(captured) == 1
    assert pkt_line(b"deepen-since 0\n") in captured[0]
    assert pkt_line(b"deepen-not old\n") in captured[0]
    assert pkt_line(b"filter blob:none\n") in captured[0]
    assert pkt_line(b"done\n") in captured[0]


def test_native_git_filtered_shallow_cutoff_round_trip(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    repo = tmp_path / "native"
    subprocess.run([git, "init", "-b", "main", str(repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run([git, "-C", str(repo), "config", "uploadpack.allowFilter", "true"], check=True)

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
    assert capabilities.feature("fetch", "shallow")
    assert capabilities.feature("fetch", "filter")

    request = build_filtered_shallow_cutoff_fetch_request(
        capabilities,
        [head],
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
    _validate_fetch_response_for_request(parsed, done=True)
    validate_shallow_response_for_request(parsed)

    assert head in parsed.shallow
    assert parsed.pack is not None
    objects = PackParser(parsed.pack).parse()
    types = sorted(obj.type_name for obj in objects.values())
    assert types == ["commit", "tree"]
    assert all(obj.type_name != "blob" for obj in objects.values())
