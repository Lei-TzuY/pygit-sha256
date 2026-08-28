from __future__ import annotations

from contextlib import contextmanager

from pygit import Repository
from pygit.fetch_cli_dry_run import run_fetch
from pygit.fetch_negotiation import (
    configured_negotiation_includes,
    has_configured_negotiation_includes,
    negotiation_remote,
    negotiation_transport,
    plan_included_haves,
)
from pygit.remote import SmartHttpClient


def _commit(repo: Repository, name: str, text: str) -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def _write_includes(repo: Repository, entries: list[tuple[str, str]]) -> None:
    lines = ["[remote]"]
    lines.extend(f"{remote}.negotiationInclude = {value}" for remote, value in entries)
    (repo.pygit_dir / "config").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_configured_negotiation_includes_preserve_order_and_duplicates(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(
        repo,
        [("origin", "main"), ("origin", "refs/heads/topic"), ("origin", "main")],
    )

    assert configured_negotiation_includes(repo, "origin") == [
        "main",
        "refs/heads/topic",
        "main",
    ]
    assert has_configured_negotiation_includes(repo) is True


def test_no_configured_negotiation_include_reports_false(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    assert configured_negotiation_includes(repo, "origin") == []
    assert has_configured_negotiation_includes(repo) is False


def test_config_include_is_added_for_active_named_remote(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "main")
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(repo, [("origin", "main")])
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    baseline = "e" * 40
    with negotiation_transport(repo, use_config_include=True):
        with negotiation_remote("origin"):
            SmartHttpClient("https://example.test/repo.git").fetch(haves={baseline})

    assert baseline in calls[0]
    assert plan_included_haves(repo, ["main"]) <= calls[0]


def test_config_includes_are_resolved_per_remote_not_per_url(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    main = _commit(repo, "a.txt", "main")
    repo.refs.set_branch("topic", main, message="test")
    _commit(repo, "b.txt", "topic")
    topic = repo.refs.resolve_head()
    repo.refs.set_branch("topic", topic, message="test")
    repo.add_remote("origin", "https://example.test/shared.git")
    repo.add_remote("backup", "https://example.test/shared.git")
    _write_includes(repo, [("origin", "main"), ("backup", "topic")])
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    with negotiation_transport(repo, use_config_include=True):
        with negotiation_remote("origin"):
            SmartHttpClient("https://example.test/shared.git").fetch(haves=[])
        with negotiation_remote("backup"):
            SmartHttpClient("https://example.test/shared.git").fetch(haves=[])

    assert calls[0] == plan_included_haves(repo, ["main"])
    assert calls[1] == plan_included_haves(repo, ["topic"])
    assert calls[0] != calls[1]


def test_direct_url_context_does_not_consume_named_remote_config(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "main")
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(repo, [("origin", "main")])
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    baseline = "d" * 40
    with negotiation_transport(repo, use_config_include=True):
        SmartHttpClient("https://example.test/repo.git").fetch(haves={baseline})

    assert calls == [{baseline}]


def test_explicit_cli_include_overrides_remote_config(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    main = _commit(repo, "a.txt", "main")
    repo.refs.set_branch("topic", main, message="test")
    topic = _commit(repo, "b.txt", "topic")
    repo.refs.set_branch("topic", topic, message="test")
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(repo, [("origin", "topic")])
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    with negotiation_transport(
        repo,
        include=["main"],
        use_config_include=True,
    ):
        with negotiation_remote("origin"):
            SmartHttpClient("https://example.test/repo.git").fetch(haves=[])

    assert calls[0] == plan_included_haves(repo, ["main"])
    assert plan_included_haves(repo, ["topic"]) != calls[0]


def test_invalid_config_only_fails_when_that_remote_is_active(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "main")
    repo.add_remote("origin", "https://example.test/origin.git")
    repo.add_remote("backup", "https://example.test/backup.git")
    _write_includes(repo, [("origin", "missing"), ("backup", "main")])

    def fake_fetch(self, haves=None, advertisement=None):
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    with negotiation_transport(repo, use_config_include=True):
        with negotiation_remote("backup"):
            assert SmartHttpClient("https://example.test/backup.git").fetch(haves=[]) == "ok"
        try:
            with negotiation_remote("origin"):
                SmartHttpClient("https://example.test/origin.git").fetch(haves=[])
        except RuntimeError as exc:
            assert "not a valid negotiation tip" in str(exc)
        else:
            raise AssertionError("invalid origin negotiationInclude should fail origin fetch")


def test_top_wrapper_enters_config_policy_without_cli_negotiation_options(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "main")
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(repo, [("origin", "main")])
    entered = []
    forwarded = []

    @contextmanager
    def policy(repo_arg, *, restrict=(), include=(), use_config_include=False):
        assert repo_arg.worktree == repo.worktree
        entered.append((list(restrict), list(include), use_config_include))
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiation_transport", policy)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: forwarded.append(list(argv)) or 0,
    )

    assert run_fetch(["origin"]) == 0
    assert forwarded == [["origin"]]
    assert entered == [([], [], True)]


def test_cli_negotiation_include_suppresses_config_fallback(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "main")
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(repo, [("origin", "missing")])
    entered = []

    @contextmanager
    def policy(repo_arg, *, restrict=(), include=(), use_config_include=False):
        entered.append((list(include), use_config_include))
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiation_transport", policy)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", lambda argv: 0)

    assert run_fetch(["--negotiation-include=main", "origin"]) == 0
    assert entered == [(["main"], False)]


def test_refetch_does_not_activate_remote_config_include(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "main")
    repo.add_remote("origin", "https://example.test/repo.git")
    _write_includes(repo, [("origin", "missing")])
    entered = []

    @contextmanager
    def refetch_scope():
        entered.append("refetch")
        yield

    @contextmanager
    def policy(*args, **kwargs):
        entered.append("negotiation")
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.refetch_transport", refetch_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiation_transport", policy)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", lambda argv: 0)

    assert run_fetch(["--refetch", "origin"]) == 0
    assert entered == ["refetch"]
