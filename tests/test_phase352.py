from __future__ import annotations

import contextlib
import importlib
import subprocess
from pathlib import Path
from typing import Dict, Iterator, Optional

import pytest

from pygit.fetch_persisted_partial import persisted_partial_filter
from pygit.objects import BlobObject
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit.pull_unborn_partial_transition import try_pull_unborn_upstream
from pygit.pull_unborn_transition import UnbornPullBootstrapError
from pygit.remote_ops import Upstream
from pygit.repo import Repository


BRANCH = "topic/empty"
SOURCE = f"refs/heads/{BRANCH}"
URL = "https://example.invalid/repo.git"
NATIVE = "1" * 40
LOCAL = "a" * 64


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _empty_partial_repo(tmp_path: Path, *, wildcard: bool = False) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", URL)

    historical = repo._read_config()
    settings = historical.setdefault("remotes", {}).setdefault("origin", {})
    settings["url"] = URL
    settings["default_branch"] = BRANCH
    repo._write_config(historical)

    (repo.pygit_dir / "HEAD").write_text(
        f"ref: refs/heads/{BRANCH}", encoding="utf-8"
    )
    repo.config_set("remote", "origin.url", URL)
    if wildcard:
        repo.config_set(
            "remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*"
        )
    repo.config_set("branch", f"{BRANCH}.remote", "origin")
    repo.config_set("branch", f"{BRANCH}.merge", SOURCE)
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "origin.promisor", "true")
    repo.config_set("remote", "origin.partialCloneFilter", "blob:none")
    return repo


def _source() -> Upstream:
    return Upstream(remote="origin", branch=BRANCH)


def test_persisted_partial_filter_is_validated(tmp_path):
    repo = _empty_partial_repo(tmp_path)
    assert persisted_partial_filter(repo, "origin") == "blob:none"

    repo.config_set("remote", "origin.partialCloneFilter", "blob:limit=17")
    assert persisted_partial_filter(repo, "origin") == "blob:limit=17"

    repo.config_set("remote", "origin.partialCloneFilter", "tree:1")
    with pytest.raises(RuntimeError, match="supports only"):
        persisted_partial_filter(repo, "origin")


def test_promisor_bit_alone_does_not_invent_filter(tmp_path):
    repo = _empty_partial_repo(tmp_path)
    repo.config_unset("remote", "origin.partialCloneFilter")
    assert persisted_partial_filter(repo, "origin") is None


def test_fetch_frontend_auto_injects_persisted_filter(tmp_path, monkeypatch):
    module = importlib.import_module("pygit.fetch_persisted_partial")
    repo = _empty_partial_repo(tmp_path)
    captured: list[list[str]] = []
    selection: list[bool] = []

    monkeypatch.setattr(module, "find_repo", lambda: repo)

    @contextlib.contextmanager
    def selected(*, allow_persistent_partial: bool = False) -> Iterator[None]:
        selection.append(allow_persistent_partial)
        yield

    monkeypatch.setattr(module, "unborn_fetch_selection", selected)
    monkeypatch.setattr(
        module,
        "_partial_run_fetch",
        lambda argv: captured.append(list(argv)) or 0,
    )
    monkeypatch.setattr(
        module,
        "_unborn_run_fetch",
        lambda argv: (_ for _ in ()).throw(AssertionError("unfiltered fallback")),
    )

    assert module.run_fetch(["origin"]) == 0
    assert captured == [["--filter=blob:none", "origin"]]
    assert selection == [True]


def test_explicit_fetch_filter_remains_authoritative(tmp_path, monkeypatch):
    module = importlib.import_module("pygit.fetch_persisted_partial")
    repo = _empty_partial_repo(tmp_path)
    repo.config_set("remote", "origin.partialCloneFilter", "blob:limit=99")
    captured: list[list[str]] = []

    monkeypatch.setattr(module, "find_repo", lambda: repo)
    monkeypatch.setattr(
        module,
        "_partial_run_fetch",
        lambda argv: captured.append(list(argv)) or 0,
    )

    assert module.run_fetch(["--filter=blob:none", "origin"]) == 0
    assert captured == [["--filter=blob:none", "origin"]]


def test_nonpartial_fetch_uses_phase335_frontend(tmp_path, monkeypatch):
    module = importlib.import_module("pygit.fetch_persisted_partial")
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", URL)
    captured: list[list[str]] = []

    monkeypatch.setattr(module, "find_repo", lambda: repo)
    monkeypatch.setattr(
        module,
        "_unborn_run_fetch",
        lambda argv: captured.append(list(argv)) or 7,
    )
    assert module.run_fetch(["origin"]) == 7
    assert captured == [["origin"]]


def test_filter_aware_selector_allows_partial_source_only_refspec(tmp_path):
    module = importlib.import_module("pygit.fetch_unborn_transition")
    repo = _empty_partial_repo(tmp_path)

    assert module._unborn_upstream_refspec(repo, "origin") is None
    spec = module._unborn_upstream_refspec(
        repo, "origin", allow_persistent_partial=True
    )
    assert spec is not None
    assert spec.source == SOURCE
    assert spec.destination is None


def _seed_filtered_target(tmp_path: Path, repo: Repository) -> tuple[str, str]:
    seed = Repository.init(str(tmp_path / "seed"))
    _write(seed.worktree, "remote.txt", "first promised blob\n")
    seed.add(["remote.txt"])
    target = seed.commit("first")
    commit = seed.store.read(target)
    tree = seed.store.read(commit.tree)
    blob = tree.entries[0].sha
    # Unit tests below mock the transport boundary, so copying complete local
    # objects is enough to exercise Phase337 preflight/checkout plumbing.  The
    # promised native identity is represented explicitly in promisor state.
    for path in seed.store.root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(seed.store.root)
            destination = repo.store.root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    return target, blob


def test_partial_first_pull_uses_filter_then_materializes_checkout_promises(
    tmp_path, monkeypatch
):
    module = importlib.import_module("pygit.pull_unborn_partial_transition")
    repo = _empty_partial_repo(tmp_path, wildcard=True)
    target, _blob = _seed_filtered_target(tmp_path, repo)
    native_blob = "2" * 40
    fetched = {"refs": {SOURCE: target}, "objects": 2}
    events: list[object] = []

    @contextlib.contextmanager
    def selected(*, allow_persistent_partial: bool = False):
        events.append(("selection", allow_persistent_partial))
        yield

    @contextlib.contextmanager
    def transport(repo_arg, remote, filter_spec, *, server_options=()):
        assert repo_arg is repo
        events.append(("transport", remote, filter_spec, tuple(server_options)))
        yield

    monkeypatch.setattr(module, "unborn_fetch_selection", selected)
    monkeypatch.setattr(module, "partial_filter_transport", transport)
    monkeypatch.setattr(module, "configured_server_options", lambda *args: ["trace=1"])
    monkeypatch.setattr(
        module,
        "fetch_porcelain",
        lambda repo_arg, remote: events.append(("fetch", remote)) or fetched,
    )
    monkeypatch.setattr(
        module,
        "_collect_checkout_promises",
        lambda repo_arg, sha: {native_blob},
    )
    monkeypatch.setattr(
        module,
        "materialize_promised_objects",
        lambda pygit_dir, oids: events.append(("materialize", tuple(oids)))
        or {native_blob: "b" * 64},
    )

    result = try_pull_unborn_upstream(repo, _source())

    assert result is not None
    assert result["status"] == "initial-pull"
    assert result["filter"] == "blob:none"
    assert result["materialized"] == {native_blob: "b" * 64}
    assert events == [
        ("selection", True),
        ("transport", "origin", "blob:none", ("trace=1",)),
        ("fetch", "origin"),
        ("materialize", (native_blob,)),
    ]
    assert repo.refs.get_branch(BRANCH) == target


def test_partial_first_pull_conflict_stops_materialization_and_branch_publication(
    tmp_path, monkeypatch
):
    module = importlib.import_module("pygit.pull_unborn_partial_transition")
    repo = _empty_partial_repo(tmp_path, wildcard=True)
    target, _blob = _seed_filtered_target(tmp_path, repo)
    _write(repo.worktree, "remote.txt", "local survives\n")

    @contextlib.contextmanager
    def passthrough(*args, **kwargs):
        yield

    monkeypatch.setattr(module, "unborn_fetch_selection", passthrough)
    monkeypatch.setattr(module, "partial_filter_transport", passthrough)
    monkeypatch.setattr(module, "configured_server_options", lambda *args: [])
    monkeypatch.setattr(
        module,
        "fetch_porcelain",
        lambda *args, **kwargs: {"refs": {SOURCE: target}, "objects": 2},
    )
    monkeypatch.setattr(
        module,
        "materialize_promised_objects",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("materialization must happen after conflict preflight")
        ),
    )

    with pytest.raises(UnbornPullBootstrapError, match="would be overwritten"):
        try_pull_unborn_upstream(repo, _source())

    assert repo.refs.get_branch(BRANCH) is None
    assert repo.refs.resolve_head() is None
    assert (repo.worktree / "remote.txt").read_text(encoding="utf-8") == "local survives\n"


def test_partial_first_pull_materialization_failure_keeps_branch_unborn(
    tmp_path, monkeypatch
):
    module = importlib.import_module("pygit.pull_unborn_partial_transition")
    repo = _empty_partial_repo(tmp_path)
    target, _blob = _seed_filtered_target(tmp_path, repo)

    @contextlib.contextmanager
    def passthrough(*args, **kwargs):
        yield

    monkeypatch.setattr(module, "unborn_fetch_selection", passthrough)
    monkeypatch.setattr(module, "partial_filter_transport", passthrough)
    monkeypatch.setattr(module, "configured_server_options", lambda *args: [])
    monkeypatch.setattr(
        module,
        "fetch_porcelain",
        lambda *args, **kwargs: {"refs": {SOURCE: target}, "objects": 2},
    )
    monkeypatch.setattr(
        module,
        "_collect_checkout_promises",
        lambda *args, **kwargs: {"2" * 40},
    )
    monkeypatch.setattr(
        module,
        "materialize_promised_objects",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyError("missing")),
    )

    with pytest.raises(UnbornPullBootstrapError, match="could not materialize"):
        try_pull_unborn_upstream(repo, _source())
    assert repo.refs.get_branch(BRANCH) is None
    assert repo.refs.resolve_head() is None


def test_promisor_without_filter_fails_before_network(tmp_path, monkeypatch):
    module = importlib.import_module("pygit.pull_unborn_partial_transition")
    repo = _empty_partial_repo(tmp_path)
    repo.config_unset("remote", "origin.partialCloneFilter")
    monkeypatch.setattr(
        module,
        "fetch_porcelain",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )

    with pytest.raises(UnbornPullBootstrapError, match="cannot infer a filter"):
        try_pull_unborn_upstream(repo, _source())


def test_nonpartial_first_pull_delegates_to_phase337(tmp_path, monkeypatch):
    module = importlib.import_module("pygit.pull_unborn_partial_transition")
    repo = Repository.init(str(tmp_path / "repo"))
    sentinel: Dict[str, object] = {"status": "ordinary"}
    monkeypatch.setattr(
        module,
        "_ordinary_try_pull_unborn_upstream",
        lambda repo_arg, source: sentinel,
    )
    assert try_pull_unborn_upstream(repo, _source()) is sentinel


def _git(*args: str, cwd: Optional[Path] = None, check: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _publish_native_first_commit(tmp_path: Path, remote: Path) -> str:
    producer = tmp_path / "producer"
    _git("init", "--object-format=sha256", "-b", BRANCH, str(producer))
    _git("config", "user.name", "Phase352", cwd=producer)
    _git("config", "user.email", "phase352@example.invalid", cwd=producer)
    _write(producer, "remote.txt", "native promised checkout\n")
    _git("add", "remote.txt", cwd=producer)
    _git("commit", "-m", "first", cwd=producer)
    first = _git("rev-parse", "HEAD", cwd=producer).stdout.strip()
    _git("remote", "add", "origin", str(remote), cwd=producer)
    _git("push", "origin", BRANCH, cwd=producer)
    return first


def _native_empty_partial_clone(tmp_path: Path, *, single: bool) -> tuple[Path, Path]:
    remote = tmp_path / ("remote-single.git" if single else "remote.git")
    _git(
        "init",
        "--bare",
        "--object-format=sha256",
        "--initial-branch=" + BRANCH,
        str(remote),
    )
    _git("config", "uploadpack.allowFilter", "true", cwd=remote)
    clone = tmp_path / ("single" if single else "default")
    args = ["clone", "--no-local", "--filter=blob:none"]
    if single:
        args.append("--single-branch")
    args.extend([f"file://{remote}", str(clone)])
    _git(*args)
    return remote, clone


def test_native_empty_partial_clone_plain_fetch_resends_filter_and_stays_unborn(tmp_path):
    remote, clone = _native_empty_partial_clone(tmp_path, single=False)
    first = _publish_native_first_commit(tmp_path, remote)

    result = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=clone,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**__import__("os").environ, "GIT_TRACE_PACKET": "1"},
        check=True,
    )
    trace = result.stderr
    assert "filter blob:none" in trace
    assert _git("rev-parse", "--verify", "HEAD", cwd=clone, check=False).returncode != 0
    assert _git(
        "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}", cwd=clone
    ).stdout.strip() == first


def test_native_single_branch_empty_partial_pull_materializes_checkout_only(tmp_path):
    remote, clone = _native_empty_partial_clone(tmp_path, single=True)
    first = _publish_native_first_commit(tmp_path, remote)

    result = subprocess.run(
        ["git", "pull"],
        cwd=clone,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**__import__("os").environ, "GIT_TRACE_PACKET": "1"},
        check=True,
    )
    assert result.stderr.count("filter blob:none") >= 1
    assert _git("rev-parse", "HEAD", cwd=clone).stdout.strip() == first
    assert (clone / "remote.txt").read_text(encoding="utf-8") == "native promised checkout\n"
    assert _git(
        "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}", cwd=clone, check=False
    ).returncode != 0
    assert _git(
        "config", "--get-all", "remote.origin.fetch", cwd=clone, check=False
    ).returncode != 0
