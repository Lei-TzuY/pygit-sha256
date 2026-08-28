from types import SimpleNamespace

import pytest

from pygit import fetch_server_option_config as config_wrapper
from pygit.fetch_negotiation import negotiation_remote
from pygit.remote import SmartHttpClient


def _repo(tmp_path, config_text, remotes=("origin",)):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    (pygit_dir / "config").write_text(config_text, encoding="utf-8")
    return SimpleNamespace(
        pygit_dir=pygit_dir,
        list_remotes=lambda: list(remotes),
        config_get=lambda section, key: None,
    )


def test_configured_server_options_preserve_order_and_reset(tmp_path):
    repo = _repo(
        tmp_path,
        """[remote]\norigin.serverOption = one\norigin.serverOption = two\norigin.serverOption =\norigin.serverOption = three\n""",
    )
    assert config_wrapper.configured_server_options(repo, "origin") == ["three"]


def test_explicit_cli_server_option_bypasses_config_fallback(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "[remote]\norigin.serverOption = configured\n")
    calls = []
    monkeypatch.setattr(config_wrapper, "find_repo", lambda: repo)
    monkeypatch.setattr(
        config_wrapper.fetch_frontend,
        "run_fetch",
        lambda argv: calls.append(list(argv)) or 0,
    )

    assert config_wrapper.run_fetch(["--server-option=cli", "origin"]) == 0
    assert calls == [["--server-option=cli", "origin"]]


def test_negotiate_only_uses_named_remote_config(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "[remote]\norigin.serverOption = one\norigin.serverOption = two\n")
    calls = []
    monkeypatch.setattr(config_wrapper, "find_repo", lambda: repo)
    monkeypatch.setattr(
        config_wrapper.fetch_frontend,
        "run_fetch",
        lambda argv: calls.append(list(argv)) or 0,
    )

    argv = ["--negotiate-only", "--negotiation-tip=main", "origin"]
    assert config_wrapper.run_fetch(argv) == 0
    assert calls == [[
        "--server-option=one",
        "--server-option=two",
        "--negotiate-only",
        "--negotiation-tip=main",
        "origin",
    ]]


def test_negotiate_only_direct_url_does_not_inherit_named_remote_config(
    monkeypatch, tmp_path
):
    repo = _repo(tmp_path, "[remote]\norigin.serverOption = configured\n")
    calls = []
    monkeypatch.setattr(config_wrapper, "find_repo", lambda: repo)
    monkeypatch.setattr(
        config_wrapper.fetch_frontend,
        "run_fetch",
        lambda argv: calls.append(list(argv)) or 0,
    )

    argv = [
        "--negotiate-only",
        "--negotiation-tip=main",
        "https://example.test/repo.git",
    ]
    assert config_wrapper.run_fetch(argv) == 0
    assert calls == [argv]


def test_shared_url_keeps_server_options_scoped_by_remote(monkeypatch, tmp_path):
    repo = _repo(
        tmp_path,
        """[remote]\none.serverOption = alpha\ntwo.serverOption = beta\n""",
        remotes=("one", "two"),
    )
    created = []

    class FakeQueryClient:
        def __init__(self, url, timeout=30, server_options=()):
            created.append(("query", url, tuple(server_options)))

        def discover_refs(self):
            return object()

    monkeypatch.setattr(config_wrapper, "SmartHttpV2QueryClient", FakeQueryClient)

    client = SmartHttpClient("https://example.test/shared.git")
    with config_wrapper.configured_server_option_transport(repo):
        with negotiation_remote("one"):
            client.discover()
        with negotiation_remote("two"):
            client.discover()

    assert created == [
        ("query", "https://example.test/shared.git", ("alpha",)),
        ("query", "https://example.test/shared.git", ("beta",)),
    ]


def test_configured_option_requires_protocol_v2(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "[remote]\norigin.serverOption = trace=1\n")

    class FallingBackQueryClient:
        def __init__(self, url, timeout=30, server_options=()):
            pass

        def discover_refs(self):
            return None

    monkeypatch.setattr(
        config_wrapper,
        "SmartHttpV2QueryClient",
        FallingBackQueryClient,
    )

    client = SmartHttpClient("https://example.test/repo.git")
    with config_wrapper.configured_server_option_transport(repo):
        with negotiation_remote("origin"):
            with pytest.raises(RuntimeError, match="server options require protocol version 2"):
                client.discover()
