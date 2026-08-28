from __future__ import annotations

from types import SimpleNamespace

from pygit.clone_cli import run_clone


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
    monkeypatch.setattr(
        "pygit.clone_cli.Repository.clone",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("depth clone must not use historical full clone")
        ),
    )

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
