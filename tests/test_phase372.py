from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pygit.clone_cli as clone_cli
import pygit.clone_unborn as clone_unborn
from pygit.clone_cli import run_clone
from pygit.clone_origin import retarget_completed_clone_remote, validate_clone_remote_name
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit.protocol_v2_unborn import ProtocolV2LsRefsResult
from pygit.remote import Advertisement
from pygit.repo import Repository


URL = "https://example.invalid/repo.git"
NATIVE_OID = "1" * 40
LOCAL_OID = "a" * 64


def _unborn_result(branch: str = "topic/empty") -> ProtocolV2LsRefsResult:
    return ProtocolV2LsRefsResult(
        Advertisement(
            refs={},
            capabilities={"ls-refs=unborn", "object-format=sha1"},
            symrefs={"HEAD": f"refs/heads/{branch}"},
        ),
        frozenset({"HEAD"}),
    )


def _install_unborn(monkeypatch, branch: str = "topic/empty") -> None:
    result = _unborn_result(branch)

    class Client:
        def __init__(self, url, timeout=30, *, server_options=()):
            self.url = url

        def discover_refs_with_unborn(self):
            return result

    monkeypatch.setattr(clone_unborn, "SmartHttpV2UnbornQueryClient", Client)


def _prepared_nonempty_repo(path: Path) -> Repository:
    repo = Repository.init(str(path))
    repo.add_remote("origin", URL)
    repo.refs.set_remote("origin", "main", LOCAL_OID)
    repo.refs.set_branch("main", LOCAL_OID)
    repo.refs.set_head_symbolic("main")
    config = repo._read_config()
    config.setdefault("remotes", {}).setdefault("origin", {})["default_branch"] = "main"
    repo._write_config(config)
    return repo


def test_validate_clone_remote_name_is_ref_safe() -> None:
    assert validate_clone_remote_name("upstream") == "upstream"
    assert validate_clone_remote_name("team/upstream") == "team/upstream"

    for value in ("", "bad name", "../escape", "bad..name", "bad@{name"):
        with pytest.raises(ValueError):
            validate_clone_remote_name(value)


def test_unborn_clone_uses_custom_remote_from_initialization(tmp_path, monkeypatch) -> None:
    _install_unborn(monkeypatch)

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=False,
        remote_name="upstream",
    )

    assert result is not None
    repo = result.repo
    assert repo.refs.get_head() == "ref: refs/heads/topic/empty"
    assert repo.list_remotes() == {"upstream": URL}
    assert repo.config_get("remote", "upstream.url") == URL
    assert repo.config_get("remote", "upstream.fetch") == (
        "+refs/heads/*:refs/remotes/upstream/*"
    )
    assert repo.config_get("branch", "topic/empty.remote") == "upstream"
    assert repo.config_get("branch", "topic/empty.merge") == "refs/heads/topic/empty"
    assert repo.config_get("remote", "origin.url") is None
    assert "origin" not in repo._read_config().get("remotes", {})
    assert repo._read_config()["remotes"]["upstream"]["default_branch"] == "topic/empty"
    assert repo.refs.list_remotes("upstream") == []


def test_unborn_single_branch_custom_remote_omits_fetch_refspec(tmp_path, monkeypatch) -> None:
    _install_unborn(monkeypatch, "main")

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=True,
        remote_name="upstream",
    )

    assert result is not None
    repo = result.repo
    assert repo.config_get("remote", "upstream.url") == URL
    assert repo.config_get("remote", "upstream.fetch") is None
    assert repo.config_get("branch", "main.remote") == "upstream"


def test_unborn_partial_clone_custom_remote_uses_custom_promisor_config_only(
    tmp_path,
    monkeypatch,
) -> None:
    _install_unborn(monkeypatch, "main")

    result = clone_unborn.try_clone_explicit_unborn_remote(
        URL,
        str(tmp_path / "clone"),
        branch_name=None,
        single_branch=False,
        filter_spec="blob:none",
        remote_name="upstream",
    )

    assert result is not None
    repo = result.repo
    assert repo.config_get("extensions", "partialClone") == "upstream"
    assert repo.config_get("remote", "upstream.promisor") == "true"
    assert repo.config_get("remote", "upstream.partialCloneFilter") == "blob:none"
    assert repo.config_get("remote", "origin.promisor") is None
    assert not (repo.pygit_dir / "promisor.json").exists()


def test_explicit_branch_error_names_custom_upstream_and_does_not_create_destination(
    tmp_path,
    monkeypatch,
) -> None:
    _install_unborn(monkeypatch)
    destination = tmp_path / "clone"

    with pytest.raises(
        RuntimeError,
        match="Remote branch topic/empty not found in upstream upstream",
    ):
        clone_unborn.try_clone_explicit_unborn_remote(
            URL,
            str(destination),
            branch_name="topic/empty",
            single_branch=False,
            remote_name="upstream",
        )

    assert not destination.exists()


def test_retarget_completed_clone_moves_tracking_and_native_map(tmp_path) -> None:
    repo = _prepared_nonempty_repo(tmp_path / "repo")
    repo._write_native_map({LOCAL_OID: NATIVE_OID}, "origin")
    repo.config_set("remote", "origin.url", URL)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo.config_set("branch", "main.remote", "origin")
    repo.config_set("branch", "main.merge", "refs/heads/main")

    retarget_completed_clone_remote(repo, "upstream")

    assert repo.list_remotes() == {"upstream": URL}
    assert repo.refs.get_remote("upstream", "main") == LOCAL_OID
    assert repo.refs.get_remote("origin", "main") is None
    assert repo._read_native_map("upstream") == {LOCAL_OID: NATIVE_OID}
    assert repo._read_native_map("origin") == {}
    assert repo.config_get("remote", "upstream.url") == URL
    assert repo.config_get("remote", "upstream.fetch") == (
        "+refs/heads/*:refs/remotes/upstream/*"
    )
    assert repo.config_get("branch", "main.remote") == "upstream"


def test_retarget_completed_partial_clone_moves_promisor_remote(tmp_path) -> None:
    repo = _prepared_nonempty_repo(tmp_path / "repo")
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "origin.promisor", "true")
    repo.config_set("remote", "origin.partialCloneFilter", "blob:none")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={NATIVE_OID: "blob"},
    )

    retarget_completed_clone_remote(repo, "upstream")

    state = read_promisor_state(repo.pygit_dir)
    assert state["remotes"] == {"upstream": {"filter": "blob:none"}}
    assert state["promised"] == {NATIVE_OID: "blob"}
    assert repo.config_get("extensions", "partialClone") == "upstream"
    assert repo.config_get("remote", "upstream.promisor") == "true"
    assert repo.config_get("remote", "upstream.partialCloneFilter") == "blob:none"
    assert repo.config_get("remote", "origin.promisor") is None


def test_cli_custom_remote_retargets_ordinary_clone(tmp_path, monkeypatch) -> None:
    repo = _prepared_nonempty_repo(tmp_path / "repo")

    monkeypatch.setattr(
        Repository,
        "clone",
        classmethod(lambda cls, url, path=None, **kwargs: repo),
    )

    assert run_clone(["-o", "upstream", URL, str(tmp_path / "ignored")]) == 0

    assert repo.list_remotes() == {"upstream": URL}
    assert repo.refs.get_remote("upstream", "main") == LOCAL_OID
    assert repo.config_get("remote", "upstream.url") == URL
    assert repo.config_get("branch", "main.remote") == "upstream"
    assert repo.config_get("branch", "main.merge") == "refs/heads/main"


def test_cli_custom_remote_retargets_shallow_override_without_hidden_preflight(
    tmp_path,
    monkeypatch,
) -> None:
    repo = _prepared_nonempty_repo(tmp_path / "repo")
    calls = []

    def fake_shallow(url, path, **kwargs):
        calls.append((url, path, kwargs))
        return repo

    monkeypatch.setattr(clone_cli, "clone_shallow_repository", fake_shallow)
    monkeypatch.setattr(
        clone_cli,
        "try_clone_explicit_unborn_remote",
        lambda *args, **kwargs: pytest.fail("override seam must skip unborn preflight"),
    )

    assert run_clone(
        ["-o", "upstream", "--depth", "1", URL, str(tmp_path / "ignored")]
    ) == 0

    assert len(calls) == 1
    assert repo.list_remotes() == {"upstream": URL}
    assert repo.refs.get_remote("upstream", "main") == LOCAL_OID


def test_cli_custom_remote_retargets_partial_override_and_promisor_state(
    tmp_path,
    monkeypatch,
) -> None:
    repo = _prepared_nonempty_repo(tmp_path / "repo")
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "origin.promisor", "true")
    repo.config_set("remote", "origin.partialCloneFilter", "blob:none")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={NATIVE_OID: "blob"},
    )

    monkeypatch.setattr(
        clone_cli,
        "clone_partial_repository",
        lambda url, path, **kwargs: repo,
    )
    monkeypatch.setattr(
        clone_cli,
        "try_clone_explicit_unborn_remote",
        lambda *args, **kwargs: pytest.fail("override seam must skip unborn preflight"),
    )

    assert run_clone(
        ["-o", "upstream", "--filter", "blob:none", URL, str(tmp_path / "ignored")]
    ) == 0

    assert read_promisor_state(repo.pygit_dir)["remotes"] == {
        "upstream": {"filter": "blob:none"}
    }
    assert repo.config_get("extensions", "partialClone") == "upstream"
    assert repo.config_get("branch", "main.remote") == "upstream"


def test_cli_invalid_custom_remote_fails_before_clone(monkeypatch) -> None:
    monkeypatch.setattr(
        clone_cli,
        "try_clone_explicit_unborn_remote",
        lambda *args, **kwargs: pytest.fail("invalid remote must fail before network"),
    )
    with pytest.raises(SystemExit, match="2"):
        run_clone(["-o", "../escape", URL])


def _git_config(repo: Path, key: str):
    completed = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", key],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        return None
    return completed.stdout.strip()


def test_native_git_empty_clone_custom_origin_default_and_single_branch(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "--object-format=sha256", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/topic/empty"],
        check=True,
    )

    default = tmp_path / "default"
    subprocess.run(
        ["git", "clone", "-o", "upstream", str(remote), str(default)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (default / ".git" / "HEAD").read_text().strip() == (
        "ref: refs/heads/topic/empty"
    )
    assert _git_config(default, "remote.upstream.url") == str(remote)
    assert _git_config(default, "remote.upstream.fetch") == (
        "+refs/heads/*:refs/remotes/upstream/*"
    )
    assert _git_config(default, "branch.topic/empty.remote") == "upstream"
    assert _git_config(default, "branch.topic/empty.merge") == "refs/heads/topic/empty"
    assert _git_config(default, "remote.origin.url") is None

    single = tmp_path / "single"
    subprocess.run(
        [
            "git",
            "clone",
            "-o",
            "upstream",
            "--single-branch",
            str(remote),
            str(single),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _git_config(single, "remote.upstream.url") == str(remote)
    assert _git_config(single, "remote.upstream.fetch") is None
    assert _git_config(single, "branch.topic/empty.remote") == "upstream"
    assert subprocess.run(
        ["git", "-C", str(single), "show-ref"],
        check=False,
        capture_output=True,
    ).stdout == b""
