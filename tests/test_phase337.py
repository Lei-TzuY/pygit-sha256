from __future__ import annotations

import importlib
import shutil
import subprocess
from pathlib import Path

import pytest

from pygit.fetch_policy import configured_fetch_refspecs
from pygit.objects import BlobObject
from pygit.pull_unborn_transition import (
    UnbornPullBootstrapError,
    try_pull_unborn_upstream,
)
from pygit.remote import Advertisement
from pygit.remote_ops import Upstream
from pygit.repo import Repository
from pygit.refs import ZERO_SHA


NATIVE = "1" * 40
BRANCH = "topic/empty"
SOURCE = f"refs/heads/{BRANCH}"
URL = "https://example.invalid/repo.git"


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _target_commit(tmp_path: Path, repo: Repository, *, nested: bool = False) -> str:
    seed = Repository.init(str(tmp_path / "seed"))
    if nested:
        _write(seed.worktree, "dir/remote.txt", "remote nested\n")
        seed.add(["dir/remote.txt"])
    else:
        _write(seed.worktree, "remote.txt", "remote first\n")
        seed.add(["remote.txt"])
    target = seed.commit("first remote commit")
    shutil.copytree(seed.store.root, repo.store.root, dirs_exist_ok=True)
    return target


def _empty_clone_repo(tmp_path: Path, *, wildcard: bool = False, partial: bool = False) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", URL)

    historical = repo._read_config()
    settings = historical.setdefault("remotes", {}).setdefault("origin", {})
    settings["url"] = URL
    settings["default_branch"] = BRANCH
    repo._write_config(historical)

    # Match Phase317/331: change symbolic HEAD without manufacturing a reflog.
    (repo.pygit_dir / "HEAD").write_text(
        f"ref: refs/heads/{BRANCH}",
        encoding="utf-8",
    )
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


def _install_fake_fetch(monkeypatch, target_sha: str, captured: list[dict[str, str]]) -> None:
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
        imported = {name: target_sha for name in source_oids}
        if source_oids:
            known_by_native[NATIVE] = target_sha
            native_map[target_sha] = NATIVE
        return imported, len(source_oids)

    monkeypatch.setattr(module, "SmartHttpClient", Client)
    monkeypatch.setattr(module, "_fetch_import_sources", importer)


def _source() -> Upstream:
    return Upstream(remote="origin", branch=BRANCH)


def test_default_empty_clone_first_pull_bootstraps_branch_and_worktree(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path, wildcard=True)
    target = _target_commit(tmp_path, repo)
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, target, captured)
    _write(repo.worktree, "keep.txt", "local untracked\n")

    result = try_pull_unborn_upstream(repo, _source())

    assert result is not None
    assert result["status"] == "initial-pull"
    assert result["sha"] == target
    assert captured == [{SOURCE: NATIVE}]
    assert repo.refs.current_branch() == BRANCH
    assert repo.refs.get_branch(BRANCH) == target
    assert repo.refs.resolve_head() == target
    assert repo.refs.get_remote("origin", BRANCH) == target
    assert (repo.worktree / "remote.txt").read_text(encoding="utf-8") == "remote first\n"
    assert (repo.worktree / "keep.txt").read_text(encoding="utf-8") == "local untracked\n"

    head_log = repo.refs.read_reflog("HEAD")
    branch_log = repo.refs.read_reflog(f"refs/heads/{BRANCH}")
    assert head_log[0].message == "initial pull"
    assert branch_log[0].message == "initial pull"
    assert head_log[0].old_sha == ZERO_SHA
    assert branch_log[0].old_sha == ZERO_SHA
    assert head_log[0].new_sha == target
    assert branch_log[0].new_sha == target

    fetch_head = (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8")
    assert fetch_head.startswith(f"{target}\t\tbranch '{BRANCH}' of {URL}\n")


def test_single_branch_first_pull_uses_fetch_head_without_tracking_ref(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path)
    target = _target_commit(tmp_path, repo)
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, target, captured)

    assert configured_fetch_refspecs(repo, "origin") == []
    result = try_pull_unborn_upstream(repo, _source())

    assert result is not None
    assert captured == [{SOURCE: NATIVE}]
    assert repo.refs.get_branch(BRANCH) == target
    assert repo.refs.get_remote("origin", BRANCH) is None
    assert configured_fetch_refspecs(repo, "origin") == []
    assert (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8").startswith(target)


def test_untracked_conflict_fetches_but_keeps_local_branch_unborn(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path, wildcard=True)
    target = _target_commit(tmp_path, repo)
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, target, captured)
    _write(repo.worktree, "remote.txt", "local survives\n")

    with pytest.raises(UnbornPullBootstrapError, match="untracked working tree files"):
        try_pull_unborn_upstream(repo, _source())

    assert captured == [{SOURCE: NATIVE}]
    assert repo.refs.get_remote("origin", BRANCH) == target
    assert repo.refs.get_branch(BRANCH) is None
    assert repo.refs.resolve_head() is None
    assert repo.refs.read_reflog("HEAD") == []
    assert repo.refs.read_reflog(f"refs/heads/{BRANCH}") == []
    assert (repo.worktree / "remote.txt").read_text(encoding="utf-8") == "local survives\n"
    assert (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8").startswith(target)


def test_symlink_ancestor_is_never_followed_by_initial_checkout(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path)
    target = _target_commit(tmp_path, repo, nested=True)
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, target, captured)

    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.worktree / "dir").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnbornPullBootstrapError, match="would be overwritten"):
        try_pull_unborn_upstream(repo, _source())

    assert repo.refs.resolve_head() is None
    assert not (outside / "remote.txt").exists()


def test_staged_state_is_preserved_and_local_branch_stays_unborn(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path, wildcard=True)
    target = _target_commit(tmp_path, repo)
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, target, captured)

    _write(repo.worktree, "staged.txt", "local staged\n")
    repo.add(["staged.txt"])

    with pytest.raises(UnbornPullBootstrapError, match="staged index entries"):
        try_pull_unborn_upstream(repo, _source())

    assert repo.index.paths() == ["staged.txt"]
    assert (repo.worktree / "staged.txt").read_text(encoding="utf-8") == "local staged\n"
    assert repo.refs.resolve_head() is None
    assert repo.refs.get_remote("origin", BRANCH) == target


def test_partial_unborn_pull_fails_before_unfiltered_network_access(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path, partial=True)
    module = importlib.import_module("pygit.pull_unborn_transition")

    def forbidden(*args, **kwargs):
        raise AssertionError("unfiltered fetch must not run")

    monkeypatch.setattr(module, "fetch_porcelain", forbidden)
    with pytest.raises(UnbornPullBootstrapError, match="requires filtered fetch support"):
        try_pull_unborn_upstream(repo, _source())

    assert repo.refs.resolve_head() is None


def test_resolved_branch_and_mismatched_upstream_do_not_activate(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path)
    target = _target_commit(tmp_path, repo)
    module = importlib.import_module("pygit.pull_unborn_transition")

    def forbidden(*args, **kwargs):
        raise AssertionError("fetch must not run")

    monkeypatch.setattr(module, "fetch_porcelain", forbidden)
    assert try_pull_unborn_upstream(repo, Upstream("origin", "other")) is None

    repo.refs.set_branch(BRANCH, target, message="resolved")
    assert try_pull_unborn_upstream(repo, _source()) is None


def test_non_commit_target_fails_after_fetch_without_resolving_branch(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path, wildcard=True)
    blob = repo.store.write(BlobObject(b"not a commit"))
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, blob, captured)

    with pytest.raises(UnbornPullBootstrapError, match="target is not a commit"):
        try_pull_unborn_upstream(repo, _source())

    assert repo.refs.resolve_head() is None
    assert repo.refs.get_branch(BRANCH) is None
    assert repo.refs.get_remote("origin", BRANCH) == blob


def test_checkout_failure_never_publishes_local_branch(tmp_path, monkeypatch):
    repo = _empty_clone_repo(tmp_path)
    target = _target_commit(tmp_path, repo)
    captured: list[dict[str, str]] = []
    _install_fake_fetch(monkeypatch, target, captured)

    def fail_checkout(sha):
        raise OSError("checkout failed")

    monkeypatch.setattr(repo, "_replace_worktree_from_commit", fail_checkout)
    with pytest.raises(OSError, match="checkout failed"):
        try_pull_unborn_upstream(repo, _source())

    assert repo.refs.resolve_head() is None
    assert repo.refs.get_branch(BRANCH) is None


def test_pull_cli_short_circuits_generic_merge_after_initial_pull(tmp_path, monkeypatch, capsys):
    module = importlib.import_module("pygit.pull_cli")
    repo = _empty_clone_repo(tmp_path)
    source = _source()
    target = "a" * 64

    monkeypatch.setattr(module, "find_repo", lambda: repo)
    monkeypatch.setattr(module, "resolve_pull_source", lambda *args, **kwargs: source)
    monkeypatch.setattr(
        module,
        "try_pull_unborn_upstream",
        lambda *args, **kwargs: {"status": "initial-pull", "sha": target},
    )
    monkeypatch.setattr(
        module,
        "fetch_configured",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("double fetch")),
    )
    monkeypatch.setattr(
        repo,
        "merge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generic merge")),
    )

    # Repository.init() is intentionally chatty; isolate the CLI invocation.
    capsys.readouterr()
    assert module.run_pull([]) == 0
    assert capsys.readouterr().out == "Pull result: initial-pull\n"


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _publish_first_native_commit(tmp_path: Path, remote: Path, branch: str) -> str:
    producer = tmp_path / "producer"
    _git("init", "--object-format=sha256", "-b", branch, str(producer))
    _git("config", "user.name", "Phase337", cwd=producer)
    _git("config", "user.email", "phase337@example.invalid", cwd=producer)
    _write(producer, "remote.txt", "native first\n")
    _git("add", "remote.txt", cwd=producer)
    _git("commit", "-m", "first", cwd=producer)
    first = _git("rev-parse", "HEAD", cwd=producer).stdout.strip()
    _git("remote", "add", "origin", str(remote), cwd=producer)
    _git("push", "origin", branch, cwd=producer)
    return first


def test_native_empty_clone_first_pull_default_and_single_branch(tmp_path):
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

    first = _publish_first_native_commit(tmp_path, remote, BRANCH)
    _git("pull", cwd=default)
    _git("pull", cwd=single)

    for clone in (default, single):
        assert _git("symbolic-ref", "HEAD", cwd=clone).stdout.strip() == SOURCE
        assert _git("rev-parse", "HEAD", cwd=clone).stdout.strip() == first
        assert (clone / "remote.txt").read_text(encoding="utf-8") == "native first\n"
        assert _git(
            "reflog", "show", "-1", "--format=%gs", "HEAD", cwd=clone
        ).stdout.strip() == "initial pull"
        assert _git(
            "reflog", "show", "-1", "--format=%gs", f"refs/heads/{BRANCH}", cwd=clone
        ).stdout.strip() == "initial pull"

    assert _git(
        "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}", cwd=default
    ).stdout.strip() == first
    assert _git(
        "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}", cwd=single, check=False
    ).returncode != 0
    assert _git(
        "config", "--get-all", "remote.origin.fetch", cwd=single, check=False
    ).returncode != 0


def test_native_untracked_conflict_leaves_local_branch_unborn_after_fetch(tmp_path):
    remote = tmp_path / "remote.git"
    _git(
        "init",
        "--bare",
        "--object-format=sha256",
        "--initial-branch=topic/empty",
        str(remote),
    )
    clone = tmp_path / "client"
    _git("clone", str(remote), str(clone))
    _write(clone, "remote.txt", "local survives\n")

    first = _publish_first_native_commit(tmp_path, remote, BRANCH)
    pulled = _git("pull", cwd=clone, check=False)

    assert pulled.returncode != 0
    assert "would be overwritten" in pulled.stderr
    assert _git("symbolic-ref", "HEAD", cwd=clone).stdout.strip() == SOURCE
    assert _git("rev-parse", "HEAD", cwd=clone, check=False).returncode != 0
    assert _git(
        "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}", cwd=clone
    ).stdout.strip() == first
    assert (clone / "remote.txt").read_text(encoding="utf-8") == "local survives\n"
