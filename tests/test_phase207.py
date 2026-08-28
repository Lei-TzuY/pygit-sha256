from __future__ import annotations

from types import SimpleNamespace

from pygit import fetch_server_option_config as config_wrapper
from pygit.fetch_cli_dry_run import _extract_server_options, _extract_shallow_options
from pygit.fetch_negotiation import negotiation_remote
from pygit.fetch_protocol_v2 import protocol_v2_transport
from pygit.fetch_shallow import ShallowFetchRequest, _ACTIVE_SHALLOW_REQUEST
from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import build_fetch_request
from pygit.remote import Advertisement, SmartHttpClient, pkt_line


def _caps():
    return ProtocolV2Capabilities(
        {
            "ls-refs": "unborn",
            "fetch": "wait-for-done shallow",
            "server-option": None,
            "object-format": "sha1",
        }
    )


def test_server_option_value_that_looks_like_shallow_option_is_not_reparsed():
    forwarded, options = _extract_server_options(
        ["--server-option", "--depth", "--deepen=2", "origin"]
    )
    assert options == ["--depth"]
    forwarded, depth, deepen, unshallow = _extract_shallow_options(forwarded)
    assert forwarded == ["origin"]
    assert depth is None
    assert deepen == 2
    assert unshallow is False


def test_fetch_request_combines_server_option_and_shallow_sections():
    body = build_fetch_request(
        _caps(),
        ["a" * 40],
        shallow=["b" * 40],
        deepen=3,
        deepen_relative=True,
        server_options=["trace=one"],
    )
    option = pkt_line(b"server-option=trace=one\n")
    shallow = pkt_line(b"shallow " + b"b" * 40 + b"\n")
    deepen = pkt_line(b"deepen 3\n")
    relative = pkt_line(b"deepen-relative\n")
    delim = body.index(b"0001")
    assert body.index(option) < delim
    assert delim < body.index(shallow) < body.index(deepen) < body.index(relative)


def test_explicit_server_option_transport_forwards_shallow_request(monkeypatch):
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    seen = []

    class FakeFetchClient:
        def __init__(self, url, timeout=30, server_options=()):
            self.server_options = tuple(server_options)

        def fetch(self, haves=None, advertisement=None, **kwargs):
            seen.append((self.server_options, tuple(haves or ()), advertisement, kwargs))
            return "result"

    monkeypatch.setattr("pygit.fetch_protocol_v2.SmartHttpV2FetchClient", FakeFetchClient)
    request = ShallowFetchRequest(
        shallow=("b" * 40,),
        deepen=4,
        deepen_relative=True,
        unshallow=False,
    )
    token = _ACTIVE_SHALLOW_REQUEST.set(request)
    try:
        with protocol_v2_transport(server_options=["trace=one"]):
            result = SmartHttpClient("https://example.test/repo.git").fetch(
                haves=["c" * 40],
                advertisement=expected,
            )
    finally:
        _ACTIVE_SHALLOW_REQUEST.reset(token)

    assert result == "result"
    assert seen == [
        (
            ("trace=one",),
            ("c" * 40,),
            expected,
            {
                "shallow": ("b" * 40,),
                "deepen": 4,
                "deepen_relative": True,
            },
        )
    ]


def test_configured_server_option_transport_forwards_shallow_per_remote(
    monkeypatch, tmp_path
):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    (pygit_dir / "config").write_text(
        "[remote]\norigin.serverOption = configured\n",
        encoding="utf-8",
    )
    repo = SimpleNamespace(
        pygit_dir=pygit_dir,
        list_remotes=lambda: ["origin"],
        config_get=lambda section, key: None,
    )
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    seen = []

    class FakeFetchClient:
        def __init__(self, url, timeout=30, server_options=()):
            self.server_options = tuple(server_options)

        def fetch(self, haves=None, advertisement=None, **kwargs):
            seen.append((self.server_options, tuple(haves or ()), advertisement, kwargs))
            return "result"

    monkeypatch.setattr(config_wrapper, "SmartHttpV2FetchClient", FakeFetchClient)
    request = ShallowFetchRequest(
        shallow=("d" * 40,),
        deepen=2,
        deepen_relative=False,
        unshallow=False,
    )
    token = _ACTIVE_SHALLOW_REQUEST.set(request)
    try:
        with config_wrapper.configured_server_option_transport(repo):
            with negotiation_remote("origin"):
                result = SmartHttpClient("https://example.test/repo.git").fetch(
                    haves=[],
                    advertisement=expected,
                )
    finally:
        _ACTIVE_SHALLOW_REQUEST.reset(token)

    assert result == "result"
    assert seen == [
        (
            ("configured",),
            (),
            expected,
            {
                "shallow": ("d" * 40,),
                "deepen": 2,
                "deepen_relative": False,
            },
        )
    ]
