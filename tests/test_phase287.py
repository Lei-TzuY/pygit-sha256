from __future__ import annotations

import pytest

from pygit.protocol_v2 import ProtocolV2Capabilities, SmartHttpV2QueryClient
from pygit.protocol_v2_object_info import (
    ObjectSizeInfo,
    SmartHttpV2ObjectInfoClient,
)


def test_object_info_reuses_capability_advertisement_across_batches(monkeypatch):
    discoveries = []
    posts = []
    capabilities = ProtocolV2Capabilities({"object-info": None})

    def fake_discover(self):
        discoveries.append(self.url)
        return capabilities

    def fake_post(self, body):
        posts.append(body)
        oid = body.split(b"oid ", 1)[1][:40].decode()
        return (ObjectSizeInfo(oid, 123),)

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_capabilities", fake_discover)
    monkeypatch.setattr(SmartHttpV2ObjectInfoClient, "_post_object_info", fake_post)

    client = SmartHttpV2ObjectInfoClient("https://example.invalid/repo.git")
    first = "1" * 40
    second = "2" * 40

    assert client.query_sizes((first,)) == {first: 123}
    assert client.query_sizes((second,)) == {second: 123}

    assert discoveries == ["https://example.invalid/repo.git"]
    assert len(posts) == 2
    assert first.encode() in posts[0]
    assert second.encode() in posts[1]


def test_object_info_caches_protocol_v0_fallback(monkeypatch):
    discoveries = []

    def fake_discover(self):
        discoveries.append(self.url)
        return None

    def fail_post(self, body):
        raise AssertionError("protocol-v0 fallback must not POST object-info")

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_capabilities", fake_discover)
    monkeypatch.setattr(SmartHttpV2ObjectInfoClient, "_post_object_info", fail_post)

    client = SmartHttpV2ObjectInfoClient("https://example.invalid/repo.git")

    assert client.query_sizes(("a" * 40,)) is None
    assert client.query_sizes(("b" * 40,)) is None
    assert discoveries == ["https://example.invalid/repo.git"]


def test_object_info_does_not_cache_discovery_exceptions(monkeypatch):
    calls = 0
    oid = "c" * 40
    capabilities = ProtocolV2Capabilities({"object-info": None})

    def flaky_discover(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary discovery failure")
        return capabilities

    def fake_post(self, body):
        return (ObjectSizeInfo(oid, 7),)

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_capabilities", flaky_discover)
    monkeypatch.setattr(SmartHttpV2ObjectInfoClient, "_post_object_info", fake_post)

    client = SmartHttpV2ObjectInfoClient("https://example.invalid/repo.git")

    with pytest.raises(OSError, match="temporary discovery failure"):
        client.query_sizes((oid,))

    assert client.query_sizes((oid,)) == {oid: 7}
    assert calls == 2


def test_object_info_capability_cache_is_per_client(monkeypatch):
    discoveries = []
    capabilities = ProtocolV2Capabilities({"object-info": None})

    def fake_discover(self):
        discoveries.append(self.url)
        return capabilities

    def fake_post(self, body):
        oid = body.split(b"oid ", 1)[1][:40].decode()
        return (ObjectSizeInfo(oid, 1),)

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_capabilities", fake_discover)
    monkeypatch.setattr(SmartHttpV2ObjectInfoClient, "_post_object_info", fake_post)

    first_client = SmartHttpV2ObjectInfoClient("https://example.invalid/one.git")
    second_client = SmartHttpV2ObjectInfoClient("https://example.invalid/two.git")

    assert first_client.query_sizes(("d" * 40,)) == {"d" * 40: 1}
    assert second_client.query_sizes(("e" * 40,)) == {"e" * 40: 1}
    assert discoveries == [
        "https://example.invalid/one.git",
        "https://example.invalid/two.git",
    ]
