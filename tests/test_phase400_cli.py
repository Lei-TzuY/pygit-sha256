"""Phase400 public clone CLI routing regressions."""

from __future__ import annotations

from types import SimpleNamespace

from pygit import Repository
from pygit import clone_cli
from pygit.clone_cli import run_clone


URL = "https://example.test/repo.git"


def test_clone_cli_explicit_tag_short_circuits_legacy_clone(tmp_path, monkeypatch, capsys):
    repo = Repository.init(str(tmp_path / "tag-clone"))
    repo.refs.set_head_detached("a" * 64, message="clone: from test")
    calls = []

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", lambda *a, **k: None)

    def fake_tag(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return SimpleNamespace(repo=repo, tag="release", commit_oid="a" * 64)

    def bomb_clone(cls, *args, **kwargs):
        raise AssertionError("legacy Repository.clone must not run after tag clone match")

    monkeypatch.setattr(clone_cli, "try_clone_explicit_tag_remote", fake_tag)
    monkeypatch.setattr(Repository, "clone", classmethod(bomb_clone))

    destination = tmp_path / "requested"
    assert run_clone(["-b", "release", URL, str(destination)]) == 0
    assert calls == [
        (
            URL,
            str(destination),
            {
                "branch_name": "release",
                "single_branch": False,
                "server_options": (),
                "checkout": True,
            },
        )
    ]
    captured = capsys.readouterr()
    assert "empty repository" not in captured.err
    assert f"Cloned {URL} into {repo.worktree}" in captured.out


def test_clone_cli_no_checkout_is_forwarded_to_tag_path(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "tag-clone"))
    repo.refs.set_head_detached("b" * 64)
    calls = []
    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", lambda *a, **k: None)

    def fake_tag(url, path, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(repo=repo, tag="release", commit_oid="b" * 64)

    monkeypatch.setattr(clone_cli, "try_clone_explicit_tag_remote", fake_tag)
    assert run_clone(["-n", "-b", "release", URL, str(tmp_path / "requested")]) == 0
    assert calls == [
        {
            "branch_name": "release",
            "single_branch": False,
            "server_options": (),
            "checkout": False,
        }
    ]


def test_clone_cli_depth_does_not_enter_phase400_tag_path(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "shallow"))
    shallow_calls = []

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", lambda *a, **k: None)

    def bomb_tag(*args, **kwargs):
        raise AssertionError("Phase400 tag path must not handle --depth")

    def fake_shallow(url, path, **kwargs):
        shallow_calls.append((url, path, kwargs))
        return repo

    monkeypatch.setattr(clone_cli, "try_clone_explicit_tag_remote", bomb_tag)
    monkeypatch.setattr(clone_cli, "clone_shallow_repository", fake_shallow)
    monkeypatch.setattr(clone_cli, "configure_clone_remote", lambda *a, **k: None)
    monkeypatch.setattr(clone_cli, "configure_clone_tracking", lambda *a, **k: None)

    assert run_clone(["--depth", "1", "-b", "release", URL, str(tmp_path / "requested")]) == 0
    assert shallow_calls[0][2]["branch_name"] == "release"
    assert shallow_calls[0][2]["depth"] == 1


def test_clone_cli_filter_does_not_enter_phase400_tag_path(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "partial"))
    partial_calls = []

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", lambda *a, **k: None)

    def bomb_tag(*args, **kwargs):
        raise AssertionError("Phase400 tag path must not handle --filter")

    def fake_partial(url, path, **kwargs):
        partial_calls.append((url, path, kwargs))
        return repo

    monkeypatch.setattr(clone_cli, "try_clone_explicit_tag_remote", bomb_tag)
    monkeypatch.setattr(clone_cli, "clone_partial_repository", fake_partial)
    monkeypatch.setattr(clone_cli, "configure_clone_remote", lambda *a, **k: None)
    monkeypatch.setattr(clone_cli, "configure_clone_tracking", lambda *a, **k: None)

    assert run_clone(["--filter", "blob:none", "-b", "release", URL, str(tmp_path / "requested")]) == 0
    assert partial_calls[0][2]["branch_name"] == "release"
    assert partial_calls[0][2]["filter_spec"] == "blob:none"
