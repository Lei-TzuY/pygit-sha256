"""Phase181 clone/fetch remote-HEAD lifecycle regressions."""

from __future__ import annotations

from types import SimpleNamespace

from pygit import Repository
from pygit.clone_cli import run_clone
from pygit.clone_remote import configure_clone_remote
from pygit.fetch_configured import fetch_configured, select_fetch_import_refs
from pygit.remote import Advertisement


def _legacy_default(repo: Repository, branch: str, remote: str = "origin") -> None:
    config = repo._read_config()
    config["remotes"][remote]["default_branch"] = branch
    repo._write_config(config)


def _clone_fixture(tmp_path):
    repo = Repository.init(str(tmp_path))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo.refs.set_remote("origin", "dev", "b" * 64)
    _legacy_default(repo, "main")
    return repo


def test_full_clone_branch_override_keeps_server_default_remote_head(tmp_path):
    repo = _clone_fixture(tmp_path)

    configure_clone_remote(
        repo,
        "https://example.test/repo.git",
        "dev",
        default_branch="main",
        single_branch=False,
    )

    assert repo.config_get("remote", "origin.url") == "https://example.test/repo.git"
    assert repo.config_get("remote", "origin.fetch") == "+refs/heads/*:refs/remotes/origin/*"
    assert repo.refs.list_remotes("origin") == ["dev", "main"]
    assert repo.refs.get_remote_head("origin") == "main"
    assert repo.refs.resolve("origin") == "a" * 64


def test_single_branch_default_clone_keeps_remote_head(tmp_path):
    repo = _clone_fixture(tmp_path)

    configure_clone_remote(
        repo,
        "https://example.test/repo.git",
        "main",
        default_branch="main",
        single_branch=True,
    )

    assert repo.refs.list_remotes("origin") == ["main"]
    assert repo.refs.get_remote_head("origin") == "main"
    assert repo.config_get("remote", "origin.fetch") == (
        "+refs/heads/main:refs/remotes/origin/main"
    )


def test_single_branch_nondefault_clone_omits_remote_head(tmp_path):
    repo = _clone_fixture(tmp_path)

    configure_clone_remote(
        repo,
        "https://example.test/repo.git",
        "dev",
        default_branch="main",
        single_branch=True,
    )

    assert repo.refs.list_remotes("origin") == ["dev"]
    assert repo.refs.get_remote_head("origin") is None
    assert repo.refs.resolve("origin") is None
    assert repo.config_get("remote", "origin.fetch") == (
        "+refs/heads/dev:refs/remotes/origin/dev"
    )


def test_configured_fetch_selector_excludes_head_and_unselected_branches(tmp_path):
    repo = Repository.init(str(tmp_path))
    repo.config_set("remote", "origin.fetch", "+refs/heads/dev:refs/remotes/origin/dev")
    refs = {
        "HEAD": "1" * 40,
        "refs/heads/main": "1" * 40,
        "refs/heads/dev": "2" * 40,
        "refs/tags/v1": "3" * 40,
    }

    assert select_fetch_import_refs(repo, "origin", refs) == {
        "refs/heads/dev": "2" * 40,
        "refs/tags/v1": "3" * 40,
    }


def test_fetch_respects_single_branch_mapping_without_retargeting_remote_head(
    tmp_path, monkeypatch
):
    repo = _clone_fixture(tmp_path)
    configure_clone_remote(
        repo,
        "https://example.test/repo.git",
        "dev",
        default_branch="main",
        single_branch=False,
    )
    repo.config_set("remote", "origin.fetch", "+refs/heads/dev:refs/remotes/origin/dev")
    repo.refs.set_remote_head("origin", "main")

    internal_main = "a" * 64
    internal_dev = "b" * 64
    native_main = "1" * 40
    native_dev = "2" * 40
    repo._write_native_map({internal_main: native_main, internal_dev: native_dev}, "origin")
    old_main = repo.refs.get_remote("origin", "main")

    class FakeClient:
        def __init__(self, url):
            assert url == "https://example.test/repo.git"

        def discover(self):
            # The server changed its default HEAD to dev. Plain fetch must not
            # rewrite the local origin/HEAD alias; remote set-head -a owns that.
            return Advertisement(
                {
                    "HEAD": native_dev,
                    "refs/heads/main": native_main,
                    "refs/heads/dev": native_dev,
                },
                set(),
                {"HEAD": "refs/heads/dev"},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("the selected dev object is already known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", FakeClient)

    result = fetch_configured(repo, "origin")

    assert result["default_branch"] == "dev"
    assert result["refs"] == {"refs/heads/dev": internal_dev}
    assert repo.refs.get_remote("origin", "main") == old_main
    assert repo.refs.get_remote("origin", "dev") == internal_dev
    assert repo.refs.get_remote_head("origin") == "main"


def test_depth_implies_single_branch_unless_explicitly_disabled(tmp_path, monkeypatch):
    calls = []
    repos = []

    def fake_clone(cls, url, path=None, depth=None, branch_name=None, single_branch=False):
        repo = Repository.init(str(tmp_path / f"repo-{len(repos)}"))
        repo.add_remote("origin", url)
        repo.refs.set_remote("origin", "main", "a" * 64)
        repo.refs.set_remote("origin", "dev", "b" * 64)
        repo.refs.set_branch("main", "a" * 64)
        repo.refs.set_head_symbolic("main")
        _legacy_default(repo, "main")
        calls.append((depth, branch_name, single_branch))
        repos.append(repo)
        return repo

    monkeypatch.setattr(Repository, "clone", classmethod(fake_clone))

    assert run_clone(["--depth", "1", "https://example.test/repo.git"]) == 0
    assert calls[-1] == (1, None, True)
    assert repos[-1].refs.list_remotes("origin") == ["main"]

    assert run_clone(
        ["--depth", "1", "--no-single-branch", "https://example.test/repo.git"]
    ) == 0
    assert calls[-1] == (1, None, False)
    assert repos[-1].refs.list_remotes("origin") == ["dev", "main"]
