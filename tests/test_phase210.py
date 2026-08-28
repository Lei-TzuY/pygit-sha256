from __future__ import annotations

from contextlib import contextmanager

import pytest

from pygit.fetch_update_shallow import (
    _extract_update_shallow,
    reject_unrequested_shallow_updates,
    run_fetch,
    update_shallow_transport,
)
from pygit.fetch_shallow import current_shallow_request, read_shallow, write_shallow
from pygit.protocol_v2_fetch import SmartHttpV2FetchClient, V2FetchResult
from pygit.remote import Advertisement
from pygit.repo import Repository


def test_extract_update_shallow_respects_option_terminator():
    forwarded, enabled = _extract_update_shallow(
        ["--update-shallow", "origin", "--", "--update-shallow"]
    )
    assert enabled is True
    assert forwarded == ["origin", "--", "--update-shallow"]


def test_default_guard_rejects_server_shallow_info(monkeypatch):
    advertisement = Advertisement({"refs/heads/main": "a" * 40}, set(), {})

    def fake_fetch(self, *args, **kwargs):
        return V2FetchResult(advertisement, {}, shallow=("b" * 40,))

    monkeypatch.setattr(SmartHttpV2FetchClient, "fetch", fake_fetch)
    with reject_unrequested_shallow_updates():
        with pytest.raises(RuntimeError, match="use --update-shallow"):
            SmartHttpV2FetchClient("https://example.test/repo.git").fetch(
                advertisement=advertisement
            )


def test_update_scope_advertises_native_boundary(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    local = "1" * 64
    native = "a" * 40
    write_shallow(repo, {local})
    repo._write_native_map({local: native}, "origin")

    with update_shallow_transport(repo, "origin"):
        request = current_shallow_request()
        assert request is not None
        assert request.shallow == (native,)
        assert request.deepen is None
        assert request.deepen_relative is False

    assert current_shallow_request() is None
    assert read_shallow(repo) == {local}


def test_run_fetch_update_shallow_enters_scope(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    events = []

    @contextmanager
    def scope(repo_arg, remote):
        assert repo_arg.worktree == repo.worktree
        events.append(("enter", remote))
        yield
        events.append("exit")

    monkeypatch.setattr("pygit.fetch_update_shallow.update_shallow_transport", scope)
    monkeypatch.setattr(
        "pygit.fetch_update_shallow._run_fetch",
        lambda argv: events.append(tuple(argv)) or 0,
    )
    monkeypatch.chdir(repo.worktree)

    assert run_fetch(["--update-shallow"]) == 0
    assert events == [("enter", "origin"), ("origin",), "exit"]


def test_run_fetch_update_shallow_requires_v2(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="protocol.version=2"):
        run_fetch(["--update-shallow", "origin"])


def test_run_fetch_rejects_incompatible_depth(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("protocol", "version", "2")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(RuntimeError, match="cannot be combined with --depth"):
        run_fetch(["--update-shallow", "--depth=2", "origin"])
