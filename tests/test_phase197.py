from __future__ import annotations

from contextlib import contextmanager

import pytest

from pygit import Repository
from pygit.fetch_cli_dry_run import _extract_negotiation_options, run_fetch
from pygit.fetch_negotiation import (
    negotiation_transport,
    plan_included_haves,
    plan_restricted_haves,
    reachable_commits,
    resolve_negotiation_tips,
)
from pygit.remote import SmartHttpClient


def _commit(repo: Repository, name: str, text: str) -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def test_extract_negotiation_options_supports_current_and_alias_names():
    forwarded, restrict, include = _extract_negotiation_options(
        [
            "--negotiation-restrict=refs/heads/main",
            "--negotiation-tip",
            "refs/heads/topic",
            "--negotiation-include=refs/heads/release",
            "origin",
        ]
    )
    assert forwarded == ["origin"]
    assert restrict == ["refs/heads/main", "refs/heads/topic"]
    assert include == ["refs/heads/release"]


def test_extract_negotiation_options_respects_option_terminator():
    forwarded, restrict, include = _extract_negotiation_options(
        ["origin", "--", "--negotiation-tip=refs/heads/main"]
    )
    assert forwarded == ["origin", "--", "--negotiation-tip=refs/heads/main"]
    assert restrict == []
    assert include == []


def test_extract_negotiation_option_requires_value():
    with pytest.raises(ValueError, match="requires a commit or ref pattern"):
        _extract_negotiation_options(["--negotiation-restrict"])


def test_resolve_exact_and_glob_negotiation_tips(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    base = _commit(repo, "a.txt", "base")
    tip = _commit(repo, "a.txt", "tip")
    repo.refs.set_branch("topic", base, message="test")

    assert resolve_negotiation_tips(repo, ["main"]) == [tip]
    assert set(resolve_negotiation_tips(repo, ["refs/heads/*"])) == {base, tip}


def test_negotiation_tip_glob_requires_match(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    with pytest.raises(RuntimeError, match="does not match any refs"):
        resolve_negotiation_tips(repo, ["refs/heads/missing-*"])


def test_reachable_commits_respects_shallow_boundary(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    root = _commit(repo, "a.txt", "root")
    boundary = _commit(repo, "a.txt", "boundary")
    tip = _commit(repo, "a.txt", "tip")
    (repo.pygit_dir / "shallow").write_text(boundary + "\n", encoding="utf-8")

    assert reachable_commits(repo, [tip]) == [tip, boundary]
    assert root not in reachable_commits(repo, [tip])


def test_restrict_plans_native_sha1_haves_for_reachable_commits(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    _commit(repo, "a.txt", "tip")

    haves = plan_restricted_haves(repo, ["main"])
    assert len(haves) == 2
    assert all(len(oid) == 40 for oid in haves)


def test_include_plans_only_exact_tip_native_oid(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    _commit(repo, "a.txt", "tip")

    included = plan_included_haves(repo, ["main"])
    restricted = plan_restricted_haves(repo, ["main"])
    assert len(included) == 1
    assert included <= restricted


def test_negotiation_transport_replaces_broad_haves_when_restricted(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    _commit(repo, "a.txt", "tip")
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    original = SmartHttpClient.fetch
    with negotiation_transport(repo, restrict=["main"]):
        assert SmartHttpClient("https://example.test/repo.git").fetch(haves={"f" * 40}) == "ok"
        assert calls[0] == plan_restricted_haves(repo, ["main"])
        assert "f" * 40 not in calls[0]
    assert SmartHttpClient.fetch is original


def test_negotiation_transport_adds_included_tip_to_existing_haves(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    _commit(repo, "a.txt", "tip")
    calls = []

    def fake_fetch(self, haves=None, advertisement=None):
        calls.append(set(haves or []))
        return "ok"

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    original_have = "e" * 40
    with negotiation_transport(repo, include=["main"]):
        SmartHttpClient("https://example.test/repo.git").fetch(haves={original_have})

    assert original_have in calls[0]
    assert plan_included_haves(repo, ["main"]) <= calls[0]


def test_negotiation_transport_restores_method_after_exception(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")

    def fake_fetch(self, haves=None, advertisement=None):
        return None

    monkeypatch.setattr(SmartHttpClient, "fetch", fake_fetch)
    original = SmartHttpClient.fetch
    with pytest.raises(RuntimeError, match="boom"):
        with negotiation_transport(repo, restrict=["main"]):
            raise RuntimeError("boom")
    assert SmartHttpClient.fetch is original


def test_run_fetch_strips_negotiation_controls_and_enters_policy(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    forwarded = []
    policies = []

    @contextmanager
    def policy(repo_arg, *, restrict=(), include=()):
        assert repo_arg.worktree == repo.worktree
        policies.append((list(restrict), list(include)))
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiation_transport", policy)
    monkeypatch.setattr(
        "pygit.fetch_cli_dry_run._run_fetch",
        lambda argv: forwarded.append(list(argv)) or 0,
    )

    assert run_fetch(
        [
            "--negotiation-tip=main",
            "--negotiation-include",
            "main",
            "origin",
        ]
    ) == 0
    assert forwarded == [["origin"]]
    assert policies == [(["main"], ["main"])]


def test_refetch_wins_over_negotiation_but_still_validates_tip(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    entered = []

    @contextmanager
    def refetch_scope():
        entered.append("refetch")
        yield

    @contextmanager
    def negotiation_scope(*args, **kwargs):
        entered.append("negotiation")
        yield

    monkeypatch.setattr("pygit.fetch_cli_dry_run.find_repo", lambda: repo)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.refetch_transport", refetch_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run.negotiation_transport", negotiation_scope)
    monkeypatch.setattr("pygit.fetch_cli_dry_run._run_fetch", lambda argv: 0)

    assert run_fetch(["--refetch", "--negotiation-tip=main", "origin"]) == 0
    assert entered == ["refetch"]

    with pytest.raises(RuntimeError, match="not a valid negotiation tip"):
        run_fetch(["--refetch", "--negotiation-tip=missing", "origin"])
