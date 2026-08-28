from __future__ import annotations

import argparse
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pygit import clone_cli, clone_shallow
from pygit.remote import Advertisement


def _fake_clone_result(tmp_path):
    return SimpleNamespace(
        refs=SimpleNamespace(current_branch=lambda: None),
        worktree=tmp_path / "clone",
    )


def test_clone_server_option_rejects_nul_and_lf():
    for value in ("bad\nvalue", "bad\x00value"):
        with pytest.raises(argparse.ArgumentTypeError, match="NUL or LF"):
            clone_cli._server_option(value)


def test_regular_clone_server_options_enter_ordered_v2_scope(tmp_path, monkeypatch):
    events = []
    result = _fake_clone_result(tmp_path)

    @contextmanager
    def v2_scope(*, server_options=()):
        events.append(("enter", tuple(server_options)))
        yield
        events.append(("exit", tuple(server_options)))

    def fake_clone(cls, url, path=None, **kwargs):
        events.append(("clone", url, path, dict(kwargs)))
        return result

    monkeypatch.setattr(clone_cli, "protocol_v2_transport", v2_scope)
    monkeypatch.setattr(clone_cli.Repository, "clone", classmethod(fake_clone))

    assert clone_cli.run_clone(
        [
            "--server-option=trace=one",
            "--server-option",
            "trace=two",
            "https://example.test/repo.git",
            str(tmp_path / "dest"),
        ]
    ) == 0

    assert events == [
        ("enter", ("trace=one", "trace=two")),
        (
            "clone",
            "https://example.test/repo.git",
            str(tmp_path / "dest"),
            {"branch_name": None, "single_branch": False},
        ),
        ("exit", ("trace=one", "trace=two")),
    ]


def test_regular_clone_without_server_options_keeps_legacy_transport_scope(
    tmp_path, monkeypatch
):
    result = _fake_clone_result(tmp_path)

    @contextmanager
    def unexpected_scope(*args, **kwargs):
        raise AssertionError("protocol_v2_transport must remain inactive")
        yield

    monkeypatch.setattr(clone_cli, "protocol_v2_transport", unexpected_scope)
    monkeypatch.setattr(
        clone_cli.Repository,
        "clone",
        classmethod(lambda cls, url, path=None, **kwargs: result),
    )

    assert clone_cli.run_clone(
        ["https://example.test/repo.git", str(tmp_path / "dest")]
    ) == 0


def test_depth_clone_forwards_server_options_to_true_shallow_path(
    tmp_path, monkeypatch
):
    calls = []
    result = _fake_clone_result(tmp_path)

    def fake_shallow(url, path, **kwargs):
        calls.append((url, path, dict(kwargs)))
        return result

    monkeypatch.setattr(clone_cli, "clone_shallow_repository", fake_shallow)

    assert clone_cli.run_clone(
        [
            "--depth=3",
            "--server-option=one",
            "--server-option=two",
            "https://example.test/repo.git",
            str(tmp_path / "dest"),
        ]
    ) == 0

    assert calls == [
        (
            "https://example.test/repo.git",
            str(tmp_path / "dest"),
            {
                "depth": 3,
                "branch_name": None,
                "single_branch": True,
                "server_options": ("one", "two"),
            },
        )
    ]


def test_depth_clone_without_server_options_preserves_phase204_call_shape(
    tmp_path, monkeypatch
):
    seen = []
    result = _fake_clone_result(tmp_path)

    def fake_shallow(
        url,
        path,
        *,
        depth,
        branch_name,
        single_branch,
    ):
        seen.append((url, path, depth, branch_name, single_branch))
        return result

    monkeypatch.setattr(clone_cli, "clone_shallow_repository", fake_shallow)

    assert clone_cli.run_clone(
        ["--depth=2", "https://example.test/repo.git", str(tmp_path / "dest")]
    ) == 0
    assert seen == [
        (
            "https://example.test/repo.git",
            str(tmp_path / "dest"),
            2,
            None,
            True,
        )
    ]


def test_shallow_clone_reuses_one_optioned_client_for_discovery_fetch_and_tags(
    tmp_path, monkeypatch
):
    native_tip = "a" * 40
    local_tip = "b" * 64
    advertisement = Advertisement(
        refs={"HEAD": native_tip, "refs/heads/main": native_tip},
        capabilities={"ls-refs", "fetch=shallow", "server-option"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    events = []

    class FakeClient:
        def __init__(self, url, timeout=30, *, server_options=()):
            self.url = url
            self.timeout = timeout
            self.server_options = tuple(server_options)
            events.append(("client", self.server_options))

        def discover_refs(self):
            events.append(("discover", self.server_options))
            return advertisement

        def fetch(self, haves=None, advertisement=None, **kwargs):
            events.append(
                (
                    "fetch",
                    self.server_options,
                    tuple(haves or ()),
                    dict(advertisement.refs),
                    dict(kwargs),
                )
            )
            return SimpleNamespace(objects={}, shallow=(), unshallow=())

    class FakeImporter:
        def __init__(self, store, objects, known=None):
            self.converted = dict(known or {})

        def import_oid(self, oid):
            self.converted[oid] = local_tip
            return local_tip

    def fake_auto_follow(repo, client, advertisement, initial_result, known, native_map):
        events.append(("tags", client.server_options, id(client)))

    monkeypatch.setattr(clone_shallow, "SmartHttpV2FetchClient", FakeClient)
    monkeypatch.setattr(clone_shallow, "StableShallowNativeImporter", FakeImporter)
    monkeypatch.setattr(clone_shallow, "_apply_shallow_response", lambda *args: None)
    monkeypatch.setattr(clone_shallow, "_auto_follow_tags", fake_auto_follow)
    monkeypatch.setattr(
        clone_shallow.Repository,
        "_replace_worktree_from_commit",
        lambda self, sha, *args, **kwargs: None,
    )

    repo = clone_shallow.clone_shallow_repository(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        depth=1,
        branch_name=None,
        single_branch=True,
        server_options=("one", "two"),
    )

    assert events[0] == ("client", ("one", "two"))
    assert events[1] == ("discover", ("one", "two"))
    assert events[2] == (
        "fetch",
        ("one", "two"),
        (),
        {"refs/heads/main": native_tip},
        {"deepen": 1},
    )
    assert events[3][0:2] == ("tags", ("one", "two"))
    assert repo.refs.get_remote("origin", "main") == local_tip
    assert repo.config_get("protocol", "version") == "2"
