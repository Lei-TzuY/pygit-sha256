from __future__ import annotations

import pytest

from pygit.repo import Repository
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage


def _repo_with_file(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    path = tmp_path / "repo" / "line\nbreak.txt"
    path.write_text("payload\n", encoding="utf-8")
    repo.add(["line\nbreak.txt"])
    commit = repo.commit("tip", author="Test <test@example.com>")
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    return repo, commit


def test_rev_list_z_ordinary_uses_sha256_and_verbatim_paths(tmp_path, monkeypatch, capsys):
    repo, commit = _repo_with_file(tmp_path, monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "-z", "HEAD"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{commit}\0")
    assert "path=line\nbreak.txt\0" in out
    identities = [field for field in out.split("\0") if len(field) == 64]
    assert identities
    assert all(all(ch in "0123456789abcdef" for ch in oid) for oid in identities)
    assert "missing=yes\0" not in out


def test_rev_list_z_ordinary_no_object_names(tmp_path, monkeypatch, capsys):
    _repo, commit = _repo_with_file(tmp_path, monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(["--objects", "--no-object-names", "-z", "HEAD"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"{commit}\0")
    assert "path=" not in out


def test_rev_list_z_ordinary_rejects_count_and_objects_edge(tmp_path, monkeypatch):
    _repo, _commit = _repo_with_file(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="not compatible with --count"):
        run_rev_list_disk_usage(["--objects", "-z", "--count", "HEAD"])
    with pytest.raises(ValueError, match="only compatible with --objects"):
        run_rev_list_disk_usage(["--objects-edge", "-z", "HEAD"])
