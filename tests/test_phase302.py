from __future__ import annotations

from email.message import Message
from weakref import WeakKeyDictionary

import pytest

from pygit import promisor_size_refresh as refresh
from pygit.promisor import promised_size, read_promisor_state, update_promisor_state
from pygit.protocol_v2 import _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE
from pygit.protocol_v2_fetch import SmartHttpV2FetchClient
from pygit.remote import pkt_line
from pygit.repo import Repository


class _Response:
    def __init__(self, body: bytes, content_type=...):
        self.body = body
        self.read_calls = 0
        if content_type is not None:
            self.headers = Message()
            if content_type is not ...:
                self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        self.read_calls += 1
        return self.body


def test_refresh_persists_valid_native_size_under_phase299_validation(
    tmp_path, monkeypatch
):
    native = "a" * 40
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/origin.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={native: "blob"},
    )

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url
            self.server_options = tuple(server_options)

        def query_sizes(self, oids):
            return {oid: 37 for oid in oids}

    monkeypatch.setattr(refresh, "_OBJECT_INFO_CLIENTS", WeakKeyDictionary())
    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    assert refresh.refresh_promisor_sizes(repo, (native,)) == {native: 37}
    assert promised_size(repo.pygit_dir, native) == 37

    local_sha256_surrogate = "b" * 64
    with pytest.raises(ValueError, match="full native SHA-1 object id"):
        update_promisor_state(
            repo.pygit_dir,
            sizes={local_sha256_surrogate: 99},
        )

    state = read_promisor_state(repo.pygit_dir)
    assert state["sizes"] == {native: 37}


def test_shared_http_discovery_and_strict_fetch_framing_compose(monkeypatch):
    native = "c" * 40
    advertisement = (
        pkt_line(b"version 2\n")
        + pkt_line(b"object-format=sha1\n")
        + pkt_line(b"fetch=shallow wait-for-done\n")
        + b"0000"
    )
    discovery = _Response(advertisement, _UPLOAD_PACK_ADVERTISEMENT_MEDIA_TYPE)
    truncated_fetch = _Response(
        pkt_line(b"acknowledgments\n") + pkt_line(f"ACK {native}\n".encode()),
        None,
    )
    responses = iter([discovery, truncated_fetch])

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: next(responses),
    )

    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    capabilities = client.discover_capabilities()
    assert capabilities is not None
    assert capabilities.supports("fetch")
    assert discovery.read_calls == 1

    with pytest.raises(ValueError, match="did not end with flush packet"):
        client._post_fetch(b"request")

    assert truncated_fetch.read_calls == 1
