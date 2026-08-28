from __future__ import annotations

from types import SimpleNamespace

from pygit.clone_cli import run_clone
from pygit.repo import Repository


def test_depth_clone_uses_true_shallow_transport(monkeypatch, tmp_path, capsys):
    calls = []
    repo = SimpleNamespace(
        worktree=tmp_path / "clone",
        refs=SimpleNamespace(current_branch=lambda: None),
    )

    def fake_shallow(url, directory, *, depth, branch_name, single_branch):
        calls.append((url, directory, depth, branch_name, single_branch))
        return repo

    monkeypatch.setattr("pygit.clone_cli.clone_shallow_repository", fake_shallow)

    assert run_clone([
        "--depth=2",
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
    ]) == 0
    assert calls == [
        (
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
            2,
            None,
            True,
        )
    ]
    assert "Cloned https://example.test/repo.git" in capsys.readouterr().out


def test_depth_clone_preserves_overridden_repository_clone_seam(monkeypatch, tmp_path):
    calls = []
    repo = SimpleNamespace(
        worktree=tmp_path / "clone",
        refs=SimpleNamespace(current_branch=lambda: None),
    )

    monkeypatch.setattr(
        "pygit.clone_cli.clone_shallow_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("overridden Repository.clone must remain authoritative")
        ),
    )

    def fake_clone(cls, url, path=None, depth=None, branch_name=None, single_branch=False):
        calls.append((url, path, depth, branch_name, single_branch))
        return repo

    monkeypatch.setattr(Repository, "clone", classmethod(fake_clone))

    assert run_clone([
        "--depth=3",
        "--no-single-branch",
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
    ]) == 0
    assert calls == [
        (
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
            3,
            None,
            False,
        )
    ]


def test_non_depth_clone_keeps_repository_clone_path(monkeypatch, tmp_path):
    calls = []
    repo = SimpleNamespace(
        worktree=tmp_path / "clone",
        refs=SimpleNamespace(current_branch=lambda: None),
    )

    monkeypatch.setattr(
        "pygit.clone_cli.clone_shallow_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ordinary clone must not use shallow transport")
        ),
    )

    def fake_clone(url, directory, **kwargs):
        calls.append((url, directory, kwargs))
        return repo

    monkeypatch.setattr("pygit.clone_cli.Repository.clone", fake_clone)

    assert run_clone([
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
    ]) == 0
    assert calls == [
        (
            "https://example.test/repo.git",
            str(tmp_path / "clone"),
            {"branch_name": None, "single_branch": False},
        )
    ]
