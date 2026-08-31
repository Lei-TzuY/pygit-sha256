from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pygit.clone_cli as clone_cli
import pygit.clone_unborn as clone_unborn
from pygit.clone_cli import run_clone
from pygit.protocol_v2_unborn import ProtocolV2LsRefsResult
from pygit.remote import Advertisement
from pygit.repo import Repository
from pygit.unborn_init import EmptyRemoteInitializationError


NATIVE_OID = "1" * 40
LOCAL_OID = "a" * 64
URL = "https://example.invalid/repo.git"


def _unborn_result(branch: str = "topic/empty") -> ProtocolV2LsRefsResult:
    return ProtocolV2LsRefsResult(
        Advertisement(
            refs={},
            capabilities={"ls-refs=unborn", "object-format=sha1"},
            symrefs={"HEAD": f"refs/heads/{branch}"},
        ),
        frozenset({"HEAD"}),
    )


def _ordinary_result() -> ProtocolV2LsRefsResult:
    return ProtocolV2LsRefsResult(
        Advertisement(
            refs={"refs/heads/main": NATIVE_OID},
            capabilities={"ls-refs=unborn", "object-format=sha1"},
            symrefs={"HEAD": "refs/heads/main"},
        ),
        frozenset(),
    )


def _install_query_result(monkeypatch, result):
    calls = []

    class Client:
        def __init__(self, url, timeout=30, *, server_options=()):
            calls.append(("init", url, timeout, tuple(server_options)))

        def discover_refs_with_unborn(self):
            calls.append(("discover",))
            return result

    monkeypatch.setattr(clone_unborn, "SmartHttpV2UnbornQueryClient", Client)
    return calls


def _object_files(repo: Repository) -> list[str]:
    return sorted(
        str(path.relative_to(repo.store.root))
        for path in repo.store.root.rglob("*")
        if path.is_file()
    )


def test_try_clone_explicit_unborn_remote_initializes_native_empty_shape(
    tmp_path,
    monkeypatch,
) -> None:
    calls = _install_query_result(monkeypatch, _unborn_result())
    destination = tmp_path / "clone"

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(destination),
        branch_name=None,
        single_branch=False,
        server_options=("trace=1", "mode=test"),
    )

    assert result is not None
    repo = result.repo
    assert result.branch == "topic/empty"
    assert calls == [
        ("init", URL, 30, ("trace=1", "mode=test")),
        ("discover",),
    ]
    assert repo.refs.get_head() == "ref: refs/heads/topic/empty"
    assert repo.refs.current_branch() == "topic/empty"
    assert repo.refs.resolve_head() is None
    assert repo.refs.list_branches() == []
    assert repo.refs.list_remotes("origin") == []
    assert repo.refs.get_remote_head("origin") is None
    assert not (repo.pygit_dir / "refs" / "heads" / "topic" / "empty").exists()
    assert not (repo.pygit_dir / "logs" / "HEAD").exists()
    assert not (repo.pygit_dir / "shallow").exists()
    assert not (repo.pygit_dir / "promisor.json").exists()
    assert _object_files(repo) == []

    assert repo.config_get("remote", "origin.url") == URL
    assert repo.config_get("remote", "origin.fetch") == (
        "+refs/heads/*:refs/remotes/origin/*"
    )
    assert repo.config_get("branch", "topic/empty.remote") == "origin"
    assert repo.config_get("branch", "topic/empty.merge") == (
        "refs/heads/topic/empty"
    )
    historical = repo._read_config()["remotes"]["origin"]
    assert historical == {"url": URL, "default_branch": "topic/empty"}


def test_single_branch_empty_clone_omits_fetch_refspec_like_native_git(
    tmp_path,
    monkeypatch,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=True,
    )

    assert result is not None
    repo = result.repo
    assert repo.config_get("remote", "origin.url") == URL
    assert repo.config_get("remote", "origin.fetch") is None
    assert repo.config_get("branch", "main.remote") == "origin"
    assert repo.config_get("branch", "main.merge") == "refs/heads/main"
    assert repo.refs.list_remotes("origin") == []


def test_depth_empty_clone_has_no_shallow_boundary_and_preserves_v2_mode(
    tmp_path,
    monkeypatch,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=True,
        depth=1,
    )

    assert result is not None
    repo = result.repo
    assert repo.config_get("protocol", "version") == "2"
    assert repo.config_get("remote", "origin.fetch") is None
    assert not (repo.pygit_dir / "shallow").exists()
    assert _object_files(repo) == []


def test_depth_no_single_branch_empty_clone_keeps_wildcard_fetch_refspec(
    tmp_path,
    monkeypatch,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=False,
        depth=1,
    )

    assert result is not None
    assert result.repo.config_get("remote", "origin.fetch") == (
        "+refs/heads/*:refs/remotes/origin/*"
    )


def test_filtered_empty_clone_persists_filter_config_without_promisor_objects(
    tmp_path,
    monkeypatch,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=False,
        filter_spec="blob:none",
    )

    assert result is not None
    repo = result.repo
    assert repo.config_get("protocol", "version") == "2"
    assert repo.config_get("extensions", "partialClone") == "origin"
    assert repo.config_get("remote", "origin.promisor") == "true"
    assert repo.config_get("remote", "origin.partialCloneFilter") == "blob:none"
    assert not (repo.pygit_dir / "promisor.json").exists()
    assert _object_files(repo) == []


def test_explicit_branch_rejects_unborn_target_before_destination_creation(
    tmp_path,
    monkeypatch,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("topic/empty"))
    destination = tmp_path / "clone"

    with pytest.raises(RuntimeError, match="Remote branch topic/empty not found"):
        clone_unborn.try_clone_explicit_unborn_remote(
            URL,
            str(destination),
            branch_name="topic/empty",
            single_branch=False,
        )

    assert not destination.exists()


def test_nonempty_v2_result_falls_back_without_local_mutation(tmp_path, monkeypatch) -> None:
    _install_query_result(monkeypatch, _ordinary_result())
    destination = tmp_path / "clone"

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(destination),
        branch_name=None,
        single_branch=False,
    )

    assert result is None
    assert not destination.exists()


def test_protocol_v0_fallback_remains_on_established_clone_path(tmp_path, monkeypatch) -> None:
    _install_query_result(monkeypatch, None)
    destination = tmp_path / "clone"

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(destination),
        branch_name=None,
        single_branch=False,
    )

    assert result is None
    assert not destination.exists()


def test_conflicting_unborn_result_fails_before_local_initialization(tmp_path, monkeypatch) -> None:
    result = ProtocolV2LsRefsResult(
        Advertisement(
            refs={"refs/heads/main": NATIVE_OID},
            capabilities=set(),
            symrefs={"HEAD": "refs/heads/main"},
        ),
        frozenset({"HEAD"}),
    )
    _install_query_result(monkeypatch, result)
    destination = tmp_path / "clone"

    with pytest.raises(EmptyRemoteInitializationError, match="concrete remote refs"):
        clone_unborn.try_clone_explicit_unborn_remote(
            URL,
            str(destination),
            branch_name=None,
            single_branch=False,
        )

    assert not destination.exists()


def test_post_init_failure_rolls_back_new_destination(tmp_path, monkeypatch) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))
    destination = tmp_path / "clone"

    def fail_initialize(repo, result):
        raise RuntimeError("injected init failure")

    monkeypatch.setattr(clone_unborn, "initialize_empty_remote_head", fail_initialize)

    with pytest.raises(RuntimeError, match="injected init failure"):
        clone_unborn.try_clone_explicit_unborn_remote(
            URL,
            str(destination),
            branch_name=None,
            single_branch=False,
        )

    assert not destination.exists()


def test_post_init_failure_restores_preexisting_empty_destination(tmp_path, monkeypatch) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))
    destination = tmp_path / "clone"
    destination.mkdir()

    def fail_initialize(repo, result):
        raise RuntimeError("injected init failure")

    monkeypatch.setattr(clone_unborn, "initialize_empty_remote_head", fail_initialize)

    with pytest.raises(RuntimeError, match="injected init failure"):
        clone_unborn.try_clone_explicit_unborn_remote(
            URL,
            str(destination),
            branch_name=None,
            single_branch=False,
        )

    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_clone_cli_short_circuits_legacy_transport_for_explicit_unborn(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("topic/empty"))

    class BombSmartHttpClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("legacy transport must not run for explicit unborn HEAD")

    monkeypatch.setattr("pygit.remote.SmartHttpClient", BombSmartHttpClient)
    destination = tmp_path / "clone"

    assert run_clone([URL, str(destination)]) == 0

    repo = Repository(str(destination))
    captured = capsys.readouterr()
    assert "warning: You appear to have cloned an empty repository." in captured.err
    assert f"Cloned {URL} into {repo.worktree}" in captured.out
    assert repo.refs.get_head() == "ref: refs/heads/topic/empty"
    assert repo.refs.resolve_head() is None
    assert repo.refs.list_branches() == []
    assert repo.config_get("branch", "topic/empty.remote") == "origin"


def test_clone_cli_depth_empty_uses_native_single_branch_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    _install_query_result(monkeypatch, _unborn_result("main"))
    destination = tmp_path / "clone"

    assert run_clone(["--depth", "1", URL, str(destination)]) == 0

    repo = Repository(str(destination))
    assert repo.refs.get_head() == "ref: refs/heads/main"
    assert repo.config_get("remote", "origin.fetch") is None
    assert repo.config_get("protocol", "version") == "2"
    assert not (repo.pygit_dir / "shallow").exists()


def test_clone_cli_preserves_repository_clone_override_without_preflight(
    tmp_path,
    monkeypatch,
) -> None:
    destination = tmp_path / "clone"
    repo = Repository.init(str(tmp_path / "fake"))
    calls = []

    def fail_preflight(*args, **kwargs):
        raise AssertionError("override seam must not perform unborn preflight")

    def fake_clone(cls, url, path=None, **kwargs):
        calls.append((url, path, kwargs))
        return repo

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", fail_preflight)
    monkeypatch.setattr(Repository, "clone", classmethod(fake_clone))
    monkeypatch.setattr(clone_cli, "configure_clone_remote", lambda *a, **k: None)
    monkeypatch.setattr(clone_cli, "configure_clone_tracking", lambda *a, **k: None)

    assert run_clone([URL, str(destination)]) == 0
    assert calls == [
        (URL, str(destination), {"branch_name": None, "single_branch": False})
    ]


def test_clone_cli_preserves_partial_clone_override_without_preflight(
    tmp_path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "fake"))
    calls = []

    def fail_preflight(*args, **kwargs):
        raise AssertionError("partial override seam must not perform unborn preflight")

    def fake_partial(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return repo

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", fail_preflight)
    monkeypatch.setattr(clone_cli, "clone_partial_repository", fake_partial)
    monkeypatch.setattr(clone_cli, "configure_clone_remote", lambda *a, **k: None)
    monkeypatch.setattr(clone_cli, "configure_clone_tracking", lambda *a, **k: None)

    assert run_clone(["--filter", "blob:none", URL, str(tmp_path / "clone")]) == 0
    assert calls[0][0] == URL
    assert calls[0][2]["filter_spec"] == "blob:none"


def test_clone_cli_preserves_shallow_clone_override_without_preflight(
    tmp_path,
    monkeypatch,
) -> None:
    repo = Repository.init(str(tmp_path / "fake"))
    calls = []

    def fail_preflight(*args, **kwargs):
        raise AssertionError("shallow override seam must not perform unborn preflight")

    def fake_shallow(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return repo

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", fail_preflight)
    monkeypatch.setattr(clone_cli, "clone_shallow_repository", fake_shallow)
    monkeypatch.setattr(clone_cli, "configure_clone_remote", lambda *a, **k: None)
    monkeypatch.setattr(clone_cli, "configure_clone_tracking", lambda *a, **k: None)

    assert run_clone(["--depth", "1", URL, str(tmp_path / "clone")]) == 0
    assert calls[0][0] == URL
    assert calls[0][2]["depth"] == 1
    assert calls[0][2]["single_branch"] is True


def _native_clone_config(repo: Path, destination: Path, *args: str) -> dict[str, str]:
    subprocess.run(
        ["git", "clone", *args, f"file://{repo}", str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    completed = subprocess.run(
        ["git", "-C", str(destination), "config", "--get-regexp", "^(remote\\.|branch\\.)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return dict(line.split(" ", 1) for line in completed.stdout.splitlines())


def test_native_git_empty_clone_metadata_baseline(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        [
            "git",
            "init",
            "--bare",
            "--object-format=sha256",
            "--initial-branch=topic/empty",
            str(remote),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    default = _native_clone_config(remote, tmp_path / "default")
    single = _native_clone_config(remote, tmp_path / "single", "--single-branch")
    depth = _native_clone_config(remote, tmp_path / "depth", "--depth", "1")
    depth_all = _native_clone_config(
        remote,
        tmp_path / "depth-all",
        "--depth",
        "1",
        "--no-single-branch",
    )

    wildcard = "+refs/heads/*:refs/remotes/origin/*"
    assert default["remote.origin.fetch"] == wildcard
    assert "remote.origin.fetch" not in single
    assert "remote.origin.fetch" not in depth
    assert depth_all["remote.origin.fetch"] == wildcard
    for config in (default, single, depth, depth_all):
        assert config["branch.topic/empty.remote"] == "origin"
        assert config["branch.topic/empty.merge"] == "refs/heads/topic/empty"


def test_native_git_explicit_branch_on_empty_remote_fails_without_destination(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    destination = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "init",
            "--bare",
            "--object-format=sha256",
            "--initial-branch=topic/empty",
            str(remote),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    completed = subprocess.run(
        [
            "git",
            "clone",
            "--branch",
            "topic/empty",
            f"file://{remote}",
            str(destination),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert completed.returncode != 0
    assert "Remote branch topic/empty not found" in completed.stderr
    assert not destination.exists()
