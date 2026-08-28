from __future__ import annotations

from pathlib import Path

from pygit.fetch_cli import run_fetch
from pygit.repo import Repository


def _repo(tmp_path: Path, monkeypatch) -> Repository:
    repo = Repository.init(str(tmp_path))
    repo.add_remote("origin", "https://example.test/repo.git")
    monkeypatch.chdir(tmp_path)
    return repo


def _result():
    return {
        "remote": "origin",
        "default_branch": "main",
        "refs": {
            "refs/heads/dev": "b" * 64,
            "refs/heads/main": "a" * 64,
        },
        "objects": 0,
        "pruned": [],
    }


def test_quiet_suppresses_success_output(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch(["--quiet", "--no-write-fetch-head", "origin"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_short_quiet_suppresses_success_output(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch(["-q", "--no-write-fetch-head", "origin"]) == 0
    assert capsys.readouterr().out == ""


def test_verbose_lists_all_fetched_refs_in_stable_order(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch(["--verbose", "--no-write-fetch-head", "origin"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Fetched 2 refs from origin"
    assert lines[1] == f" {'b' * 12} refs/heads/dev from origin"
    assert lines[2] == f" {'a' * 12} refs/heads/main from origin"


def test_short_verbose_is_supported(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch(["-v", "--no-write-fetch-head", "origin"]) == 0
    assert "refs/heads/main from origin" in capsys.readouterr().out


def test_last_quiet_or_verbose_option_wins_like_native_git(tmp_path, monkeypatch, capsys):
    _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch(["-q", "-v", "--no-write-fetch-head", "origin"]) == 0
    assert "refs/heads/main from origin" in capsys.readouterr().out

    assert run_fetch(["-v", "-q", "--no-write-fetch-head", "origin"]) == 0
    assert capsys.readouterr().out == ""


def test_quiet_multi_fetch_hides_per_remote_progress(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    repo.add_remote("backup", "https://example.test/backup.git")
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch([
        "--quiet",
        "--no-write-fetch-head",
        "--multiple",
        "origin",
        "backup",
    ]) == 0
    assert capsys.readouterr().out == ""


def test_verbose_multi_fetch_keeps_source_progress_and_ref_details(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, monkeypatch)
    repo.add_remote("backup", "https://example.test/backup.git")
    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", lambda *a, **k: _result())

    assert run_fetch([
        "--verbose",
        "--no-write-fetch-head",
        "--multiple",
        "origin",
        "backup",
    ]) == 0
    output = capsys.readouterr().out
    assert "Fetching origin" in output
    assert "Fetching backup" in output
    assert "refs/heads/main from origin" in output
    assert "refs/heads/main from backup" in output
