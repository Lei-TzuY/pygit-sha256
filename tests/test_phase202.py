from __future__ import annotations

from contextlib import contextmanager

import pytest

from pygit.fetch_cli_dry_run import _extract_shallow_options, run_fetch
from pygit.fetch_protocol_v2 import protocol_v2_transport
from pygit.fetch_shallow import (
    INFINITE_DEPTH,
    _apply_shallow_response,
    _fetch_import_sources_shallow,
    current_shallow_request,
    read_shallow,
    shallow_fetch_transport,
    write_shallow,
)
from pygit.protocol_v2 import ProtocolV2Capabilities, SmartHttpV2QueryClient
from pygit.protocol_v2_fetch import (
    SmartHttpV2FetchClient,
    V2FetchResult,
    build_fetch_request,
)
from pygit.remote import Advertisement, FetchResult, SmartHttpClient, pkt_line
from pygit.repo import Repository


def _caps(fetch_value: str = "shallow wait-for-done") -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "ls-refs": "unborn",
            "fetch": fetch_value,
            "object-format": "sha1",
        }
    )


def test_fetch_request_frames_shallow_depth():
    shallow = "b" * 40
    body = build_fetch_request(
        _caps(),
        ["a" * 40],
        shallow=[shallow],
        deepen=7,
    )

    assert pkt_line(f"shallow {shallow}\n".encode()) in body
    assert pkt_line(b"deepen 7\n") in body
    assert pkt_line(b"deepen-relative\n") not in body


def test_fetch_request_frames_relative_deepen():
    body = build_fetch_request(
        _caps(),
        ["a" * 40],
        shallow=["b" * 40],
        deepen=3,
        deepen_relative=True,
    )
    assert pkt_line(b"deepen 3\n") in body
    assert pkt_line(b"deepen-relative\n") in body


def test_shallow_request_requires_advertised_feature():
    with pytest.raises(RuntimeError, match="advertise shallow"):
        build_fetch_request(
            _caps("wait-for-done"),
            ["a" * 40],
            deepen=2,
        )


def test_deepen_relative_requires_deepen():
    with pytest.raises(ValueError, match="requires deepen"):
        build_fetch_request(
            _caps(),
            ["a" * 40],
            deepen_relative=True,
        )


def test_v2_fetch_result_preserves_shallow_info(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    client = SmartHttpV2FetchClient("https://example.test/repo.git")
    shallow = "b" * 40
    unshallow = "c" * 40
    seen = {}

    monkeypatch.setattr(client, "discover_capabilities", lambda: _caps())

    def fake_post(body):
        from pygit.protocol_v2_fetch import ProtocolV2FetchResponse

        seen["body"] = body
        # Empty native pack with a valid header/checksum is cumbersome here;
        # patch PackParser instead so this test stays focused on metadata.
        return ProtocolV2FetchResponse(
            acknowledgments=(),
            ready=False,
            nak=False,
            shallow=(shallow,),
            unshallow=(unshallow,),
            wanted_refs={},
            pack=b"PACK-test",
        )

    monkeypatch.setattr(client, "_post_fetch", fake_post)
    monkeypatch.setattr("pygit.protocol_v2_fetch.PackParser.parse", lambda self: {})

    result = client.fetch(
        advertisement=advertisement,
        shallow=[unshallow],
        deepen=2,
        deepen_relative=True,
    )
    assert isinstance(result, V2FetchResult)
    assert result.shallow == (shallow,)
    assert result.unshallow == (unshallow,)
    assert pkt_line(b"deepen-relative\n") in seen["body"]


def test_shallow_file_round_trip_and_remove(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    a = "a" * 64
    b = "b" * 64

    write_shallow(repo, {b, a})
    assert read_shallow(repo) == {a, b}
    assert (repo.pygit_dir / "shallow").read_text(encoding="utf-8") == f"{a}\n{b}\n"

    write_shallow(repo, set())
    assert not (repo.pygit_dir / "shallow").exists()


def test_apply_shallow_response_translates_native_ids(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    old_local = "1" * 64
    new_local = "2" * 64
    old_native = "a" * 40
    new_native = "b" * 40
    write_shallow(repo, {old_local})

    result = V2FetchResult(
        Advertisement({}, set(), {}),
        {},
        shallow=(new_native,),
        unshallow=(old_native,),
    )
    _apply_shallow_response(
        repo,
        result,
        {old_native: old_local, new_native: new_local},
    )
    assert read_shallow(repo) == {new_local}


def test_shallow_import_forces_exchange_even_for_known_tip(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    local = "1" * 64
    native = "a" * 40
    advertisement = Advertisement({"refs/heads/main": native}, set(), {})
    calls = []

    class Client:
        def fetch(self, haves=None, advertisement=None):
            calls.append((list(haves or []), advertisement.refs.copy()))
            return V2FetchResult(advertisement, {})

    imported, count = _fetch_import_sources_shallow(
        repo,
        Client(),
        advertisement,
        {"refs/heads/main": native},
        {local: native},
        {native: local},
    )
    assert calls == [([], {"refs/heads/main": native})]
    assert imported == {"refs/heads/main": local}
    assert count == 0


def test_deepen_scope_requires_existing_shallow_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    with pytest.raises(RuntimeError, match="existing shallow repository"):
        with shallow_fetch_transport(repo, "origin", deepen=1):
            pass


def test_unshallow_uses_infinite_depth_and_native_boundary(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    local = "1" * 64
    native = "a" * 40
    write_shallow(repo, {local})
    repo._write_native_map({local: native}, "origin")

    with shallow_fetch_transport(repo, "origin", unshallow=True):
        request = current_shallow_request()
        assert request is not None
        assert request.shallow == (native,)
        assert request.deepen == INFINITE_DEPTH
        assert request.deepen_relative is False
        assert request.unshallow is True

    assert current_shallow_request() is None


def test_protocol_scope_forwards_shallow_only_on_first_transfer(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})
    repo = Repository.init(".") if False else None  # type aid without filesystem use
    calls = []

    @contextmanager
    def active_request():
        from pygit.fetch_shallow import _ACTIVE_SHALLOW_REQUEST, ShallowFetchRequest

        token = _ACTIVE_SHALLOW_REQUEST.set(
            ShallowFetchRequest(("b" * 40,), 2, True, False)
        )
        try:
            yield
        finally:
            _ACTIVE_SHALLOW_REQUEST.reset(token)

    def fake_fetch(self, haves=None, advertisement=None, **kwargs):
        calls.append(kwargs)
        return FetchResult(advertisement, {})

    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", fake_fetch)

    with active_request(), protocol_v2_transport():
        client = SmartHttpClient("https://example.test/repo.git")
        client.fetch(advertisement=advertisement)
        client.fetch(advertisement=advertisement)

    assert calls[0] == {
        "shallow": ("b" * 40,),
        "deepen": 2,
        "deepen_relative": True,
    }
    assert calls[1] == {}


def test_protocol_scope_rejects_v0_fallback_for_shallow(monkeypatch):
    @contextmanager
    def active_request():
        from pygit.fetch_shallow import _ACTIVE_SHALLOW_REQUEST, ShallowFetchRequest

        token = _ACTIVE_SHALLOW_REQUEST.set(ShallowFetchRequest((), 2, False, False))
        try:
            yield
        finally:
            _ACTIVE_SHALLOW_REQUEST.reset(token)

    monkeypatch.setattr(SmartHttpV2QueryClient, "discover_refs", lambda self: None)
    with active_request(), protocol_v2_transport():
        with pytest.raises(RuntimeError, match="requires protocol version 2"):
            SmartHttpClient("https://example.test/repo.git").discover()


def test_extract_shallow_options_respects_option_terminator():
    forwarded, depth, deepen, unshallow = _extract_shallow_options(
        ["--depth=3", "origin", "--", "--deepen=9"]
    )
    assert forwarded == ["origin", "--", "--deepen=9"]
    assert depth == 3
    assert deepen is None
    assert unshallow is False


def test_run_fetch_strips_depth_and_enters_scopes(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    events = []

    @contextmanager
    def protocol_scope():
        events.append("v2-enter")
        yield
        events.append("v2-exit")

    @contextmanager
    def shallow_scope(repo_arg, remote, *, depth=None, deepen=None, unshallow=False):
        assert repo_arg.worktree == repo.worktree
        events.append(("shallow-enter", remote, depth, deepen, unshallow))
        yield
        events.append("shallow-exit")

    monkeypatch.setattr("pygit.fetch_cli_dry_run.protocol_v2_transport", protocol_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.shallow_fetch_transport", shallow_scope)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: events.append(tuple(argv)) or 0,
    )
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(["--depth=4"]) == 0
    assert events == [
        "v2-enter",
        ("shallow-enter", "origin", 4, None, False),
        ("origin",),
        "shallow-exit",
        "v2-exit",
    ]


def test_run_fetch_shallow_requires_protocol_v2(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="protocol.version=2"):
        run_fetch(["--depth=2", "origin"])


def test_run_fetch_rejects_shallow_with_refetch(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="cannot be combined with --refetch"):
        run_fetch(["--depth=2", "--refetch", "origin"])
