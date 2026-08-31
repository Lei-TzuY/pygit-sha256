from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pygit import Repository
from pygit.push_atomic import AtomicPushResult, AtomicRefUpdate
from pygit.push_defaults import PushSpec
from pygit.push_tracking import (
    _match_refspec,
    tracking_branch_for_push,
    update_tracking_after_push,
)
from pygit.push_transport import push_atomic_specs, push_ref
from pygit.remote import Advertisement, PushResult


ZERO_NATIVE = "0" * 40


def _commit(repo: Repository, name: str = "file.txt", text: str = "hello\n") -> str:
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(
        "initial",
        author_name="Phase355",
        author_email="phase355@example.com",
    )


def _repo(tmp_path: Path) -> tuple[Repository, str]:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/repo.git")
    return repo, _commit(repo)


def _modern_remote_without_fetch(repo: Repository) -> None:
    repo.config_set("remote", "origin.url", "https://example.invalid/repo.git")
    repo.config_unset("remote", "origin.fetch")


class _SinglePushClient:
    refs = {}

    def __init__(self, url: str) -> None:
        self.url = url

    def discover(self) -> Advertisement:
        return Advertisement(dict(self.refs), {"report-status"}, {})

    def push(self, ref_name, new_oid, objects, advertisement=None):
        adv = advertisement or self.discover()
        old_oid = adv.refs.get(ref_name, ZERO_NATIVE)
        return PushResult(adv, ref_name, old_oid, new_oid, len(objects))


class _AtomicPushClient:
    refs = {}

    def __init__(self, url: str) -> None:
        self.url = url

    def discover(self) -> Advertisement:
        return Advertisement(dict(self.refs), {"atomic", "report-status"}, {})

    def push_many(self, updates, objects, advertisement=None):
        adv = advertisement or self.discover()
        records = tuple(
            AtomicRefUpdate(ref, adv.refs.get(ref, ZERO_NATIVE), new_oid)
            for ref, new_oid in updates
        )
        return AtomicPushResult(adv, records, len(objects))


def test_match_refspec_exact_and_wildcard():
    assert (
        _match_refspec(
            "refs/heads/topic",
            "+refs/heads/*:refs/remotes/origin/*",
        )
        == "refs/remotes/origin/topic"
    )
    assert (
        _match_refspec(
            "refs/heads/topic",
            "+refs/heads/topic:refs/remotes/origin/selected",
        )
        == "refs/remotes/origin/selected"
    )
    assert _match_refspec("refs/heads/other", "refs/heads/topic:refs/remotes/origin/topic") is None


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "^refs/heads/topic",
        "refs/heads/*:refs/remotes/origin/topic",
        "refs/heads/topic",
        "refs/heads/topic:",
    ],
)
def test_match_refspec_malformed_or_negative_fails_closed(spec):
    assert _match_refspec("refs/heads/topic", spec) is None


def test_legacy_remote_without_git_style_config_preserves_same_name_tracking(tmp_path):
    repo, _ = _repo(tmp_path)
    assert repo.config_get("remote", "origin.url") is None
    assert tracking_branch_for_push(repo, "origin", "refs/heads/main") == "main"


def test_modern_remote_requires_configured_fetch_refspec(tmp_path):
    repo, _ = _repo(tmp_path)
    _modern_remote_without_fetch(repo)
    assert tracking_branch_for_push(repo, "origin", "refs/heads/main") is None

    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    assert tracking_branch_for_push(repo, "origin", "refs/heads/main") == "main"

    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/main:refs/remotes/origin/published",
    )
    assert tracking_branch_for_push(repo, "origin", "refs/heads/main") == "published"


def test_tracking_branch_rejects_destinations_outside_remote_namespace(tmp_path):
    repo, _ = _repo(tmp_path)
    repo.config_set("remote", "origin.url", "https://example.invalid/repo.git")
    repo.config_set("remote", "origin.fetch", "refs/heads/*:refs/heads/cache/*")
    assert tracking_branch_for_push(repo, "origin", "refs/heads/main") is None


def test_update_tracking_after_push_only_mutates_mapped_ref(tmp_path):
    repo, oid = _repo(tmp_path)
    _modern_remote_without_fetch(repo)
    assert update_tracking_after_push(repo, "origin", "refs/heads/main", oid) is None
    assert repo.refs.get_remote("origin", "main") is None

    repo.config_set("remote", "origin.fetch", "+refs/heads/main:refs/remotes/origin/cache")
    assert update_tracking_after_push(repo, "origin", "refs/heads/main", oid) == "cache"
    assert repo.refs.get_remote("origin", "cache") == oid
    assert repo.refs.get_remote("origin", "main") is None

    assert update_tracking_after_push(repo, "origin", "refs/heads/main", None) == "cache"
    assert repo.refs.get_remote("origin", "cache") is None


def test_repository_push_without_fetch_refspec_does_not_create_tracking_ref(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    _modern_remote_without_fetch(repo)
    import pygit.remote as remote_module

    monkeypatch.setattr(remote_module, "SmartHttpPushClient", _SinglePushClient)
    result = repo.push("origin")

    assert result["status"] == "pushed"
    assert result["sha"] == oid
    assert repo.refs.get_remote("origin", "main") is None
    native_map = repo._read_native_map("origin")
    assert oid in native_map
    assert len(native_map[oid]) == 40


def test_repository_push_legacy_remote_still_updates_same_name_tracking(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    import pygit.remote as remote_module

    monkeypatch.setattr(remote_module, "SmartHttpPushClient", _SinglePushClient)
    assert repo.push("origin")["status"] == "pushed"
    assert repo.refs.get_remote("origin", "main") == oid


def test_repository_push_restores_stale_tracking_when_unmapped(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    _modern_remote_without_fetch(repo)
    stale = "a" * 64
    repo.refs.set_remote("origin", "main", stale)
    import pygit.remote as remote_module

    monkeypatch.setattr(remote_module, "SmartHttpPushClient", _SinglePushClient)
    assert repo.push("origin")["status"] == "pushed"
    assert repo.refs.get_remote("origin", "main") == stale
    assert repo.refs.get_branch("main") == oid


def test_repository_push_updates_fetch_mapped_alias(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    repo.config_set("remote", "origin.url", "https://example.invalid/repo.git")
    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/main:refs/remotes/origin/published",
    )
    import pygit.remote as remote_module

    monkeypatch.setattr(remote_module, "SmartHttpPushClient", _SinglePushClient)
    assert repo.push("origin")["status"] == "pushed"
    assert repo.refs.get_remote("origin", "main") is None
    assert repo.refs.get_remote("origin", "published") == oid


def test_push_ref_respects_missing_and_exact_fetch_refspec(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    _modern_remote_without_fetch(repo)
    import pygit.push_transport as transport

    monkeypatch.setattr(transport, "SmartHttpPushClient", _SinglePushClient)
    result = push_ref(repo, "origin", "refs/heads/main", "refs/heads/topic")
    assert result["status"] == "pushed"
    assert repo.refs.get_remote("origin", "topic") is None

    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/topic:refs/remotes/origin/topic",
    )
    result = push_ref(repo, "origin", "refs/heads/main", "refs/heads/topic")
    assert result["status"] == "pushed"
    assert repo.refs.get_remote("origin", "topic") == oid


def test_atomic_push_updates_only_fetch_mapped_branch(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    repo.refs.set_branch("other", oid)
    repo.config_set("remote", "origin.url", "https://example.invalid/repo.git")
    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/main:refs/remotes/origin/main",
    )
    import pygit.push_transport as transport

    monkeypatch.setattr(transport, "AtomicSmartHttpPushClient", _AtomicPushClient)
    results = push_atomic_specs(
        repo,
        "origin",
        (PushSpec("main", "main"), PushSpec("other", "other")),
    )

    assert [result[1]["status"] for result in results] == ["pushed", "pushed"]
    assert repo.refs.get_remote("origin", "main") == oid
    assert repo.refs.get_remote("origin", "other") is None


def test_atomic_delete_preserves_unmapped_tracking_ref(tmp_path, monkeypatch):
    repo, oid = _repo(tmp_path)
    existing_native = "1" * 40
    repo.refs.set_remote("origin", "other", oid)
    repo._write_native_map({oid: existing_native}, "origin")
    repo.config_set("remote", "origin.url", "https://example.invalid/repo.git")
    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/main:refs/remotes/origin/main",
    )

    class ExistingAtomic(_AtomicPushClient):
        refs = {"refs/heads/other": existing_native}

    import pygit.push_transport as transport

    monkeypatch.setattr(transport, "AtomicSmartHttpPushClient", ExistingAtomic)
    results = push_atomic_specs(
        repo,
        "origin",
        (PushSpec("", "other", namespace="heads", delete=True),),
        force=True,
    )
    assert results[0][1]["status"] == "deleted"
    assert repo.refs.get_remote("origin", "other") == oid


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _native_first_push(tmp_path: Path, name: str, *, single_branch: bool):
    remote = tmp_path / f"{name}.git"
    clone = tmp_path / f"{name}-clone"
    _git("init", "--bare", "--initial-branch=topic/empty", str(remote))
    argv = ["clone"]
    if single_branch:
        argv.append("--single-branch")
    argv.extend([str(remote), str(clone)])
    _git(*argv)
    _git("config", "user.name", "Phase355", cwd=clone)
    _git("config", "user.email", "phase355@example.com", cwd=clone)
    (clone / "file.txt").write_text("hello\n", encoding="utf-8")
    _git("add", "file.txt", cwd=clone)
    _git("commit", "-m", "first", cwd=clone)
    _git("push", "origin", cwd=clone)
    return clone


def test_native_git_empty_clone_first_push_tracking_depends_on_fetch_refspec(tmp_path):
    default = _native_first_push(tmp_path, "default", single_branch=False)
    single = _native_first_push(tmp_path, "single", single_branch=True)

    default_fetch = _git("config", "--get", "remote.origin.fetch", cwd=default)
    assert default_fetch.stdout.strip() == "+refs/heads/*:refs/remotes/origin/*"
    assert _git("rev-parse", "refs/remotes/origin/topic/empty", cwd=default).returncode == 0

    single_fetch = _git("config", "--get", "remote.origin.fetch", cwd=single, check=False)
    assert single_fetch.returncode != 0
    tracking = _git(
        "rev-parse",
        "--verify",
        "refs/remotes/origin/topic/empty",
        cwd=single,
        check=False,
    )
    assert tracking.returncode != 0
