"""Phase 63 tests: read-only smart-HTTP remote ref inspection."""

from pathlib import Path

import pytest

from pygit import Repository, ls_remote, resolve_remote_url
from pygit.launcher import _run_ls_remote
from pygit.remote import Advertisement


URL = "https://example.test/repo.git"


def _advertisement() -> Advertisement:
    return Advertisement(
        refs={
            "HEAD": "a" * 40,
            "refs/heads/feature": "b" * 40,
            "refs/heads/main": "a" * 40,
            "refs/notes/build": "e" * 40,
            "refs/tags/v1.0": "c" * 40,
            "refs/tags/v1.0^{}": "d" * 40,
        },
        capabilities={"symref=HEAD:refs/heads/main"},
        symrefs={"HEAD": "refs/heads/main"},
    )


def _mock_discovery(monkeypatch) -> None:
    monkeypatch.setattr(
        "pygit.remote_query.SmartHttpClient.discover",
        lambda self: _advertisement(),
    )


def test_ls_remote_filters_heads_tags_refs_and_tail_patterns(monkeypatch) -> None:
    _mock_discovery(monkeypatch)

    heads = ls_remote(URL, heads=True)
    assert [(ref.oid, ref.name) for ref in heads.refs] == [
        ("b" * 40, "refs/heads/feature"),
        ("a" * 40, "refs/heads/main"),
    ]

    tags = ls_remote(URL, tags=True)
    assert [ref.name for ref in tags.refs] == ["refs/tags/v1.0", "refs/tags/v1.0^{}"]

    refs_only = ls_remote(URL, refs_only=True)
    assert "HEAD" not in {ref.name for ref in refs_only.refs}
    assert "refs/tags/v1.0^{}" not in {ref.name for ref in refs_only.refs}

    matched = ls_remote(URL, patterns=("main", "v1*"))
    assert [ref.name for ref in matched.refs] == [
        "refs/heads/main",
        "refs/tags/v1.0",
        "refs/tags/v1.0^{}",
    ]


def test_ls_remote_reports_symrefs_and_keeps_native_sha1(monkeypatch) -> None:
    _mock_discovery(monkeypatch)

    result = ls_remote(URL)

    assert result.symrefs == (("HEAD", "refs/heads/main"),)
    assert all(len(ref.oid) == 40 for ref in result.refs)
    assert result.refs[0].name == "HEAD"
    assert result.refs[0].oid == "a" * 40


def test_configured_remote_query_does_not_mutate_repository(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    repo.add_remote("origin", URL)
    capsys.readouterr()
    _mock_discovery(monkeypatch)
    remotes_before = repo.list_remotes().copy()

    result = ls_remote("origin", repo=repo, heads=True)

    assert result.url == URL
    assert repo.list_remotes() == remotes_before
    assert repo.refs.list_remotes("origin") == []
    assert not list(repo.store.all_shas())


def test_cli_symref_filters_exit_code_and_get_url(tmp_path: Path, monkeypatch, capsys) -> None:
    _mock_discovery(monkeypatch)

    assert _run_ls_remote(["--symref", URL]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ref: refs/heads/main\tHEAD"
    assert f"{'a' * 40}\tHEAD" in lines

    assert _run_ls_remote(["--heads", URL]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{'b' * 40}\trefs/heads/feature",
        f"{'a' * 40}\trefs/heads/main",
    ]

    assert _run_ls_remote(["--exit-code", URL, "does-not-exist*"]) == 2
    assert capsys.readouterr().out == ""

    repo = Repository.init(str(tmp_path / "r"))
    repo.add_remote("origin", URL)
    monkeypatch.chdir(repo.worktree)
    capsys.readouterr()
    monkeypatch.setattr(
        "pygit.remote_query.SmartHttpClient.discover",
        lambda self: (_ for _ in ()).throw(AssertionError("--get-url must not contact remote")),
    )
    assert _run_ls_remote(["--get-url", "origin"]) == 0
    assert capsys.readouterr().out.strip() == URL


def test_remote_source_validation(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))

    assert resolve_remote_url(URL) == URL
    with pytest.raises(ValueError, match="smart HTTP"):
        resolve_remote_url("ssh://example.test/repo.git")
    with pytest.raises(KeyError, match="Unknown remote"):
        resolve_remote_url("origin")
    with pytest.raises(KeyError, match="Unknown remote"):
        resolve_remote_url("missing", repo)
