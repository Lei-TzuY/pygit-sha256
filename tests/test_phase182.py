from __future__ import annotations

from pygit.config import GitConfig
from pygit.fetch_configured import configured_fetch_refspecs, select_fetch_import_refs
from pygit.remote_branches import set_remote_branches
from pygit.remote_cli import run_remote
from pygit.remote_lifecycle import add_remote
from pygit.repo import Repository


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    add_remote(repo, "origin", "https://example.invalid/repo.git")
    return repo


def _fetch_values(repo: Repository):
    return GitConfig(repo.pygit_dir).get_all("remote", "origin.fetch")


def test_set_branches_replaces_default_mapping_in_order(tmp_path):
    repo = _repo(tmp_path)

    result = set_remote_branches(repo, "origin", ["main", "dev"])

    assert result == [
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/dev:refs/remotes/origin/dev",
    ]
    assert _fetch_values(repo) == result
    assert GitConfig(repo.pygit_dir).get("remote", "origin.url") == "https://example.invalid/repo.git"


def test_set_branches_add_appends_and_preserves_duplicates(tmp_path):
    repo = _repo(tmp_path)
    set_remote_branches(repo, "origin", ["main", "main"])

    set_remote_branches(repo, "origin", ["main", "release"], add=True)

    assert _fetch_values(repo) == [
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/release:refs/remotes/origin/release",
    ]


def test_set_branches_accepts_literal_and_wildcard_tokens_like_native_git(tmp_path):
    repo = _repo(tmp_path)

    set_remote_branches(repo, "origin", ["feature/*", "refs/heads/main", "^topic"])

    assert _fetch_values(repo) == [
        "+refs/heads/feature/*:refs/remotes/origin/feature/*",
        "+refs/heads/refs/heads/main:refs/remotes/origin/refs/heads/main",
        "+refs/heads/^topic:refs/remotes/origin/^topic",
    ]


def test_empty_replacement_clears_fetch_list_without_deleting_tracking_refs(tmp_path):
    repo = _repo(tmp_path)
    repo.refs.set_remote("origin", "main", "a" * 64)

    set_remote_branches(repo, "origin", [])

    assert _fetch_values(repo) == []
    assert configured_fetch_refspecs(repo, "origin") == []
    assert repo.refs.get_remote("origin", "main") == "a" * 64


def test_add_with_empty_branch_list_is_a_noop(tmp_path):
    repo = _repo(tmp_path)
    before = _fetch_values(repo)

    assert set_remote_branches(repo, "origin", [], add=True) == []

    assert _fetch_values(repo) == before


def test_set_branches_rejects_unknown_remote(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))

    try:
        set_remote_branches(repo, "missing", ["main"])
    except KeyError as exc:
        assert "No such remote" in str(exc)
    else:
        raise AssertionError("unknown remote should fail")


def test_configured_fetch_selector_obeys_set_branches_replacement(tmp_path):
    repo = _repo(tmp_path)
    set_remote_branches(repo, "origin", ["main", "feature/*"])
    advertised = {
        "HEAD": "0" * 40,
        "refs/heads/main": "1" * 40,
        "refs/heads/dev": "2" * 40,
        "refs/heads/feature/a": "3" * 40,
        "refs/heads/feature/b": "4" * 40,
        "refs/tags/v1": "5" * 40,
    }

    selected = select_fetch_import_refs(repo, "origin", advertised)

    assert selected == {
        "refs/heads/main": "1" * 40,
        "refs/heads/feature/a": "3" * 40,
        "refs/heads/feature/b": "4" * 40,
        "refs/tags/v1": "5" * 40,
    }


def test_empty_set_branches_stops_branch_selection_but_keeps_tag_behavior(tmp_path):
    repo = _repo(tmp_path)
    set_remote_branches(repo, "origin", [])

    selected = select_fetch_import_refs(
        repo,
        "origin",
        {
            "HEAD": "0" * 40,
            "refs/heads/main": "1" * 40,
            "refs/heads/dev": "2" * 40,
            "refs/tags/v1": "3" * 40,
        },
    )

    assert selected == {"refs/tags/v1": "3" * 40}


def test_legacy_json_only_remote_retains_all_heads_fetch_fallback(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")

    assert configured_fetch_refspecs(repo, "origin") == [
        "+refs/heads/*:refs/remotes/origin/*"
    ]


def test_remote_cli_set_branches_replace_and_add(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)

    assert run_remote(["set-branches", "origin", "main", "dev"]) == 0
    assert run_remote(["set-branches", "--add", "origin", "release"]) == 0

    reopened = Repository(str(repo.worktree))
    assert _fetch_values(reopened) == [
        "+refs/heads/main:refs/remotes/origin/main",
        "+refs/heads/dev:refs/remotes/origin/dev",
        "+refs/heads/release:refs/remotes/origin/release",
    ]
