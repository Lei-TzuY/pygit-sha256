from __future__ import annotations

from contextlib import contextmanager

import pytest

from pygit.fetch_cli_dry_run import _extract_server_options, run_fetch
from pygit.fetch_protocol_v2 import protocol_v2_transport
from pygit.protocol_v2 import ProtocolV2Capabilities, build_ls_refs_request
from pygit.protocol_v2_fetch import build_fetch_request
from pygit.remote import Advertisement, SmartHttpClient, pkt_line
from pygit.repo import Repository


def _caps(*, server_option: bool = True) -> ProtocolV2Capabilities:
    values = {
        "ls-refs": "unborn",
        "fetch": "wait-for-done",
        "object-format": "sha1",
    }
    if server_option:
        values["server-option"] = None
    return ProtocolV2Capabilities(values)


def test_ls_refs_server_options_preserve_command_line_order():
    body = build_ls_refs_request(
        _caps(),
        server_options=["trace=one", "trace=two"],
    )
    first = body.index(pkt_line(b"server-option=trace=one\n"))
    second = body.index(pkt_line(b"server-option=trace=two\n"))
    delim = body.index(b"0001")
    assert first < second < delim


def test_fetch_server_options_live_in_capability_section():
    body = build_fetch_request(
        _caps(),
        ["a" * 40],
        server_options=["one", "two"],
    )
    assert pkt_line(b"server-option=one\n") in body
    assert pkt_line(b"server-option=two\n") in body
    assert body.index(pkt_line(b"server-option=two\n")) < body.index(b"0001")
    assert body.index(b"0001") < body.index(pkt_line(b"want " + b"a" * 40 + b"\n"))


def test_server_option_requires_advertised_capability():
    with pytest.raises(RuntimeError, match="does not advertise server-option"):
        build_ls_refs_request(_caps(server_option=False), server_options=["x"])


def test_server_option_rejects_nul_and_lf():
    for value in ["bad\nvalue", "bad\x00value"]:
        with pytest.raises(ValueError, match="NUL or LF"):
            build_fetch_request(_caps(), ["a" * 40], server_options=[value])


def test_cli_extracts_short_and_long_server_options_in_order():
    forwarded, options = _extract_server_options(
        ["-o", "one", "--server-option=two", "--server-option", "three", "origin"]
    )
    assert forwarded == ["origin"]
    assert options == ["one", "two", "three"]


def test_cli_option_terminator_keeps_literal_server_option_refspec():
    forwarded, options = _extract_server_options(
        ["--server-option=one", "origin", "--", "--server-option=literal"]
    )
    assert options == ["one"]
    assert forwarded == ["origin", "--", "--server-option=literal"]


def test_cli_server_option_enters_v2_scope_without_protocol_config(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    events = []

    @contextmanager
    def v2_scope(*, server_options=()):
        events.append(("v2", tuple(server_options)))
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.protocol_v2_transport", v2_scope)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: events.append(("fetch", tuple(argv))) or 0,
    )
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(["--server-option=one", "-o", "two", "origin"]) == 0
    assert events == [("v2", ("one", "two")), ("fetch", ("origin",))]


def test_protocol_v2_transport_rejects_legacy_fallback_with_server_option(monkeypatch):
    monkeypatch.setattr(
        "pygit.protocol_v2.SmartHttpV2QueryClient.discover_refs",
        lambda self: None,
    )

    with protocol_v2_transport(server_options=["one"]):
        with pytest.raises(RuntimeError, match="require protocol version 2"):
            SmartHttpClient("https://example.test/repo.git").discover()


def test_protocol_v2_transport_passes_options_to_query_client(monkeypatch):
    expected = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    seen = []

    def fake_refs(self, *args, **kwargs):
        seen.append(self.server_options)
        return expected

    monkeypatch.setattr(
        "pygit.protocol_v2.SmartHttpV2QueryClient.discover_refs",
        fake_refs,
    )

    with protocol_v2_transport(server_options=["one", "two"]):
        actual = SmartHttpClient("https://example.test/repo.git").discover()

    assert actual is expected
    assert seen == [("one", "two")]


def test_existing_protocol_v2_scope_call_shape_is_preserved(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.config_set("protocol", "version", "2")
    events = []

    @contextmanager
    def v2_scope():
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr("pygit.fetch_cli_dry_run.protocol_v2_transport", v2_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", lambda argv: 0)
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(["origin"]) == 0
    assert events == ["enter", "exit"]
