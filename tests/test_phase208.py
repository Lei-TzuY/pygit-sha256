from __future__ import annotations

from contextlib import contextmanager

import pytest

from pygit.fetch_shallow import write_shallow
from pygit.fetch_shallow_selectors import (
    _parse_shallow_since,
    _selector_fetch_request,
    current_selector_request,
    extract_shallow_selectors,
    run_fetch,
    shallow_selector_transport,
)
from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.remote import pkt_line
from pygit.repo import Repository


def _caps() -> ProtocolV2Capabilities:
    return ProtocolV2Capabilities(
        {
            "ls-refs": "unborn",
            "fetch": "shallow wait-for-done",
            "server-option": "",
            "object-format": "sha1",
        }
    )


def test_parse_shallow_since_accepts_epoch_and_iso_utc():
    assert _parse_shallow_since("1704067200") == 1704067200
    assert _parse_shallow_since("2024-01-01T00:00:00Z") == 1704067200


def test_extract_selectors_preserves_repeat_order_and_terminator():
    forwarded, since, excludes = extract_shallow_selectors(
        [
            "--shallow-since=1704067200",
            "--shallow-exclude=refs/tags/v1",
            "--shallow-exclude",
            "refs/heads/legacy",
            "origin",
            "--",
            "--shallow-exclude=literal",
        ]
    )
    assert since == 1704067200
    assert excludes == ["refs/tags/v1", "refs/heads/legacy"]
    assert forwarded == ["origin", "--", "--shallow-exclude=literal"]


def test_extract_rejects_duplicate_since_and_injected_exclude():
    with pytest.raises(ValueError, match="only once"):
        extract_shallow_selectors(
            ["--shallow-since=1", "--shallow-since=2", "origin"]
        )
    with pytest.raises(ValueError, match="valid remote ref"):
        extract_shallow_selectors(["--shallow-exclude=x\ny", "origin"])


def test_selector_request_frames_since_excludes_and_server_option():
    body = _selector_fetch_request(
        _caps(),
        ["a" * 40],
        shallow=["b" * 40],
        deepen_since=1704067200,
        deepen_not=["refs/tags/v1", "refs/heads/legacy"],
        server_options=["trace=1"],
    )
    assert pkt_line(b"server-option=trace=1\n") in body
    assert pkt_line(b"deepen-since 1704067200\n") in body
    assert pkt_line(b"deepen-not refs/tags/v1\n") in body
    assert pkt_line(b"deepen-not refs/heads/legacy\n") in body
    assert body.index(b"deepen-since") < body.index(b"want ")


def test_selector_request_requires_shallow_feature():
    caps = ProtocolV2Capabilities({"fetch": "wait-for-done", "object-format": "sha1"})
    with pytest.raises(RuntimeError, match="advertise shallow"):
        _selector_fetch_request(
            caps,
            ["a" * 40],
            deepen_since=1,
            deepen_not=[],
        )


def test_selector_scope_requires_existing_shallow_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    with pytest.raises(RuntimeError, match="existing shallow repository"):
        with shallow_selector_transport(repo, "origin", since=1, excludes=[]):
            pass


def test_selector_scope_translates_sha256_boundary_and_restores(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    local = "1" * 64
    native = "a" * 40
    write_shallow(repo, {local})
    repo._write_native_map({local: native}, "origin")

    assert current_selector_request() is None
    with shallow_selector_transport(
        repo,
        "origin",
        since=1704067200,
        excludes=["refs/tags/v1"],
    ):
        request = current_selector_request()
        assert request is not None
        assert request.shallow == (native,)
        assert request.deepen is None
        assert request.deepen_relative is False
        assert request.deepen_since == 1704067200
        assert request.deepen_not == ("refs/tags/v1",)
    assert current_selector_request() is None


def test_run_fetch_strips_selectors_and_uses_default_remote(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    local = "1" * 64
    native = "a" * 40
    write_shallow(repo, {local})
    repo._write_native_map({local: native}, "origin")
    events = []

    @contextmanager
    def scope(repo_arg, remote, *, since, excludes):
        assert repo_arg.worktree == repo.worktree
        events.append(("enter", remote, since, tuple(excludes)))
        yield
        events.append("exit")

    monkeypatch.setattr("pygit.fetch_shallow_selectors.shallow_selector_transport", scope)
    monkeypatch.setattr(
        "pygit.fetch_shallow_selectors._run_fetch",
        lambda argv: events.append(tuple(argv)) or 0,
    )
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(
        ["--shallow-since=1704067200", "--shallow-exclude=refs/tags/v1"]
    ) == 0
    assert events == [
        ("enter", "origin", 1704067200, ("refs/tags/v1",)),
        ("origin",),
        "exit",
    ]


def test_run_fetch_rejects_depth_combination(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(RuntimeError, match="cannot be combined"):
        run_fetch(["--shallow-since=1", "--deepen=2", "origin"])


def test_literal_selector_after_terminator_is_not_intercepted(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "pygit.fetch_shallow_selectors._run_fetch",
        lambda argv: seen.append(list(argv)) or 0,
    )
    assert run_fetch(["origin", "--", "--shallow-since=1"]) == 0
    assert seen == [["origin", "--", "--shallow-since=1"]]
