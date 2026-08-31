from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

from pygit.fetch_policy import configured_fetch_refspecs
from pygit.fetch_porcelain import fetch_porcelain
from pygit.fetch_unborn_transition import (
    _unborn_upstream_refspec,
    unborn_fetch_selection,
)
from pygit.remote import Advertisement
from pygit.repo import Repository


NATIVE = "1" * 40
LOCAL = "a" * 64
BRANCH = "topic/empty"
SOURCE = f"refs/heads/{BRANCH}"
URL = "https://example.invalid/repo.git"


def _empty_clone_repo(tmp_path, *, wildcard=False, partial=False):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", URL)

    historical = repo._read_config()
    settings = historical.setdefault("remotes", {}).setdefault("origin", {})
    settings["url"] = URL
    settings["default_branch"] = BRANCH
    repo._write_config(historical)

    repo.refs.set_head_symbolic(BRANCH, message="clone: empty remote")
    repo.config_set("remote", "origin.url", URL)
    if wildcard:
        repo.config_set(
            "remote",
            "origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        )
    repo.config_set("branch", f"{BRANCH}.remote", "origin")
    repo.config_set("branch", f"{BRANCH}.merge", SOURCE)

    if partial:
        repo.config_set("extensions", "partialClone", "origin")
        repo.config_set("remote", "origin.promisor", "true")
        repo.config_set("remote", "origin.partialCloneFilter", "blob:none")
    return repo


def _install_fake_fetch(monkeypatch, captured):
    module = importlib.import_module("pygit.fetch_configured")

    class Client:
        def __init__(self, url):
            self.url = url

        def discover(self):
            return Advertisement(
                refs={SOURCE: NATIVE},
                capabilities=set(),
                symrefs={"HEAD": SOURCE},
            )

    def importer(repo, client, advertisement, source_oids, native_map, known_by_native):
        captured.append(dict(source_oids))
        imported = {name: LOCAL for name in source_oids}
        if source_oids:
            known_by_native[NATIVE] = LOCAL
            native_map[LOCAL] = NATIVE
        return imported, len(source_oids)

    monkeypatch.setattr(module, "SmartHttpClient", Client)
    monkeypatch.setattr(module, "_fetch_import_sources", importer)


def test_unborn_single_branch_fetches_upstream_only_into_fetch_head(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path)
    captured = []
    _install_fake_fetch(monkeypatch, captured)

    assert configured_fetch_refspecs(repo, "origin") == []
    with unborn_fetch_selection():
        result = fetch_porcelain(repo, "origin")

    assert captured == [{SOURCE: NATIVE}]
    assert result["refs"] == {SOURCE: LOCAL}
    assert repo.refs.get_branch(BRANCH) is None
    assert repo.refs.get_remote("origin", BRANCH) is None
    assert configured_fetch_refspecs(repo, "origin") == []
    assert repo._read_native_map("origin") == {LOCAL: NATIVE}

    fetch_head = (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8")
    assert fetch_head.startswith(f"{LOCAL}\t\tbranch '{BRANCH}' of {URL}\n")


def test_default_empty_clone_keeps_normal_remote_tracking_destination(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path, wildcard=True)
    captured = []
    _install_fake_fetch(monkeypatch, captured)

    with unborn_fetch_selection():
        result = fetch_porcelain(repo, "origin")

    assert captured == [{SOURCE: NATIVE}]
    assert result["refs"] == {SOURCE: LOCAL}
    assert repo.refs.get_branch(BRANCH) is None
    assert repo.refs.get_remote("origin", BRANCH) == LOCAL
    assert [spec.raw for spec in configured_fetch_refspecs(repo, "origin")] == [
        "+refs/heads/*:refs/remotes/origin/*"
    ]


def test_resolved_branch_does_not_override_intentionally_empty_fetch_list(tmp_path):
    repo = _empty_clone_repo(tmp_path)
    repo.refs.set_branch(BRANCH, "b" * 64, message="resolved")

    assert _unborn_upstream_refspec(repo, "origin") is None
    assert configured_fetch_refspecs(repo, "origin") == []


def test_unborn_fallback_requires_matching_upstream_and_historical_default(tmp_path):
    repo = _empty_clone_repo(tmp_path)

    repo.config_set("branch", f"{BRANCH}.remote", "backup")
    assert _unborn_upstream_refspec(repo, "origin") is None

    repo.config_set("branch", f"{BRANCH}.remote", "origin")
    repo.config_set("branch", f"{BRANCH}.merge", "refs/heads/other")
    assert _unborn_upstream_refspec(repo, "origin") is None

    repo.config_set("branch", f"{BRANCH}.merge", SOURCE)
    historical = repo._read_config()
    historical["remotes"]["origin"]["default_branch"] = "other"
    repo._write_config(historical)
    assert _unborn_upstream_refspec(repo, "origin") is None


def test_partial_unborn_clone_does_not_fall_back_to_unfiltered_fetch(tmp_path):
    repo = _empty_clone_repo(tmp_path, partial=True)

    assert _unborn_upstream_refspec(repo, "origin") is None
    assert configured_fetch_refspecs(repo, "origin") == []


def test_command_scoped_projection_restores_configured_parser(tmp_path):
    module = importlib.import_module("pygit.fetch_configured")
    repo = _empty_clone_repo(tmp_path)
    original = module._parsed_fetch_refspecs

    with pytest.raises(RuntimeError, match="boom"):
        with unborn_fetch_selection():
            selected = module._parsed_fetch_refspecs(repo, "origin")
            assert len(selected) == 1
            assert selected[0].source == SOURCE
            assert selected[0].destination is None
            raise RuntimeError("boom")

    assert module._parsed_fetch_refspecs is original
    assert configured_fetch_refspecs(repo, "origin") == []


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def test_native_empty_clone_first_fetch_default_and_single_branch(tmp_path):
    remote = tmp_path / "remote.git"
    _git(
        "init",
        "--bare",
        "--object-format=sha256",
        "--initial-branch=topic/empty",
        str(remote),
    )

    default = tmp_path / "default"
    single = tmp_path / "single"
    _git("clone", str(remote), str(default))
    _git("clone", "--single-branch", str(remote), str(single))

    producer = tmp_path / "producer"
    _git("init", "--object-format=sha256", "-b", "topic/empty", str(producer))
    _git("config", "user.name", "Phase335", cwd=producer)
    _git("config", "user.email", "phase335@example.invalid", cwd=producer)
    (producer / "f.txt").write_text("first\n", encoding="utf-8")
    _git("add", "f.txt", cwd=producer)
    _git("commit", "-m", "first", cwd=producer)
    first = _git("rev-parse", "HEAD", cwd=producer).stdout.strip()
    assert len(first) == 64
    _git("remote", "add", "origin", str(remote), cwd=producer)
    _git("push", "origin", "topic/empty", cwd=producer)

    _git("fetch", "origin", cwd=default)
    _git("fetch", "origin", cwd=single)

    assert _git(
        "rev-parse",
        "--verify",
        "refs/remotes/origin/topic/empty",
        cwd=default,
    ).stdout.strip() == first
    assert _git(
        "rev-parse",
        "--verify",
        "refs/heads/topic/empty",
        cwd=default,
        check=False,
    ).returncode != 0

    assert _git(
        "config",
        "--get-all",
        "remote.origin.fetch",
        cwd=single,
        check=False,
    ).returncode != 0
    assert _git(
        "rev-parse",
        "--verify",
        "refs/remotes/origin/topic/empty",
        cwd=single,
        check=False,
    ).returncode != 0
    assert _git(
        "rev-parse",
        "--verify",
        "refs/heads/topic/empty",
        cwd=single,
        check=False,
    ).returncode != 0

    default_fetch_head = (default / ".git" / "FETCH_HEAD").read_text(encoding="utf-8")
    single_fetch_head = (single / ".git" / "FETCH_HEAD").read_text(encoding="utf-8")
    assert default_fetch_head.split("\t", 1)[0] == first
    assert single_fetch_head.split("\t", 1)[0] == first
    assert "branch 'topic/empty'" in single_fetch_head
