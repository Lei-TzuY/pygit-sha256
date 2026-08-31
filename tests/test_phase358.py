from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import pygit.clone_cli as clone_cli
import pygit.clone_partial as clone_partial
import pygit.clone_shallow as clone_shallow
import pygit.clone_unborn as clone_unborn
import pygit.clone_v2_reuse as clone_v2_reuse
from pygit.clone_unborn import CloneRefDiscovery
from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.protocol_v2_fetch import ProtocolV2FetchResponse
from pygit.protocol_v2_unborn import ProtocolV2LsRefsResult
from pygit.remote import Advertisement
from pygit.repo import Repository


URL = "https://example.invalid/repo.git"
MAIN_NATIVE = "1" * 40
DEV_NATIVE = "2" * 40


def _capabilities(*, filtered: bool = True) -> ProtocolV2Capabilities:
    fetch = "shallow wait-for-done filter" if filtered else "shallow wait-for-done"
    return ProtocolV2Capabilities(
        {
            "ls-refs": "unborn",
            "fetch": fetch,
            "object-format": "sha1",
        }
    )


def _unborn_discovery(branch: str = "topic/empty") -> CloneRefDiscovery:
    capabilities = _capabilities()
    advertisement = Advertisement(
        refs={},
        capabilities={
            "ls-refs=unborn",
            "fetch=shallow wait-for-done filter",
            "object-format=sha1",
        },
        symrefs={"HEAD": f"refs/heads/{branch}"},
    )
    return CloneRefDiscovery(
        ProtocolV2LsRefsResult(advertisement, frozenset({"HEAD"})),
        capabilities,
    )


def _ordinary_discovery() -> CloneRefDiscovery:
    capabilities = _capabilities()
    advertisement = Advertisement(
        refs={
            "HEAD": MAIN_NATIVE,
            "refs/heads/main": MAIN_NATIVE,
            "refs/heads/dev": DEV_NATIVE,
        },
        capabilities={
            "ls-refs=unborn",
            "fetch=shallow wait-for-done filter",
            "object-format=sha1",
        },
        symrefs={"HEAD": "refs/heads/main"},
    )
    return CloneRefDiscovery(
        ProtocolV2LsRefsResult(advertisement, frozenset()),
        capabilities,
    )


def _object_files(repo: Repository) -> list[str]:
    return sorted(
        str(path.relative_to(repo.store.root))
        for path in repo.store.root.rglob("*")
        if path.is_file()
    )


def test_clone_discovery_retains_capabilities_without_breaking_query_seam(monkeypatch) -> None:
    capabilities = _capabilities()
    result = _unborn_discovery().refs
    calls = []

    class Client:
        def __init__(self, url, timeout=30, *, server_options=()):
            calls.append((url, timeout, tuple(server_options)))
            self._last_capabilities = capabilities

        def discover_refs_with_unborn(self):
            calls.append("discover")
            return result

    monkeypatch.setattr(clone_unborn, "SmartHttpV2UnbornQueryClient", Client)
    discovery = clone_unborn.discover_clone_refs_with_unborn(
        URL,
        server_options=("trace=1",),
    )

    assert discovery is not None
    assert discovery.refs is result
    assert discovery.capabilities is capabilities
    assert calls == [(URL, 30, ("trace=1",)), "discover"]


def test_direct_partial_clone_accepts_explicit_unborn_without_fetch(tmp_path, monkeypatch) -> None:
    calls = []

    def discover(url, *, server_options=()):
        calls.append((url, tuple(server_options)))
        return _unborn_discovery("topic/empty")

    def bomb_fetch(*args, **kwargs):
        raise AssertionError("empty partial clone must not fetch a pack")

    monkeypatch.setattr(clone_partial, "discover_clone_refs_with_unborn", discover)
    monkeypatch.setattr(clone_partial, "fetch_filtered_discovered_clone", bomb_fetch)

    repo = clone_partial.clone_partial_repository(
        URL,
        str(tmp_path / "clone"),
        filter_spec="blob:none",
        branch_name=None,
        single_branch=False,
        server_options=("one", "two"),
    )

    assert calls == [(URL, ("one", "two"))]
    assert repo.refs.get_head() == "ref: refs/heads/topic/empty"
    assert repo.refs.resolve_head() is None
    assert repo.refs.list_branches() == []
    assert repo.refs.list_remotes("origin") == []
    assert repo.config_get("protocol", "version") == "2"
    assert repo.config_get("extensions", "partialClone") == "origin"
    assert repo.config_get("remote", "origin.promisor") == "true"
    assert repo.config_get("remote", "origin.partialCloneFilter") == "blob:none"
    assert repo.config_get("remote", "origin.fetch") == (
        "+refs/heads/*:refs/remotes/origin/*"
    )
    assert not (repo.pygit_dir / "promisor.json").exists()
    assert not (repo.pygit_dir / "shallow").exists()
    assert _object_files(repo) == []


def test_direct_shallow_clone_accepts_explicit_unborn_without_fetch(tmp_path, monkeypatch) -> None:
    calls = []

    def discover(url, *, server_options=()):
        calls.append((url, tuple(server_options)))
        return _unborn_discovery("main")

    def bomb_fetch(*args, **kwargs):
        raise AssertionError("empty shallow clone must not fetch a pack")

    monkeypatch.setattr(clone_shallow, "discover_clone_refs_with_unborn", discover)
    monkeypatch.setattr(clone_shallow, "fetch_discovered_clone", bomb_fetch)

    repo = clone_shallow.clone_shallow_repository(
        URL,
        str(tmp_path / "clone"),
        depth=3,
        branch_name=None,
        single_branch=True,
        server_options=("trace=1",),
    )

    assert calls == [(URL, ("trace=1",))]
    assert repo.refs.get_head() == "ref: refs/heads/main"
    assert repo.refs.resolve_head() is None
    assert repo.refs.list_branches() == []
    assert repo.refs.list_remotes("origin") == []
    assert repo.config_get("protocol", "version") == "2"
    assert repo.config_get("remote", "origin.fetch") is None
    assert not (repo.pygit_dir / "shallow").exists()
    assert not (repo.pygit_dir / "promisor.json").exists()
    assert _object_files(repo) == []


@pytest.mark.parametrize("kind", ["partial", "shallow"])
def test_direct_clone_explicit_branch_rejects_unborn_and_cleans_destination(
    kind,
    tmp_path,
    monkeypatch,
) -> None:
    destination = tmp_path / "clone"
    discovery = _unborn_discovery("topic/empty")

    if kind == "partial":
        monkeypatch.setattr(
            clone_partial,
            "discover_clone_refs_with_unborn",
            lambda *a, **k: discovery,
        )
        call = lambda: clone_partial.clone_partial_repository(
            URL,
            str(destination),
            filter_spec="blob:none",
            branch_name="topic/empty",
            single_branch=False,
        )
    else:
        monkeypatch.setattr(
            clone_shallow,
            "discover_clone_refs_with_unborn",
            lambda *a, **k: discovery,
        )
        call = lambda: clone_shallow.clone_shallow_repository(
            URL,
            str(destination),
            depth=1,
            branch_name="topic/empty",
            single_branch=True,
        )

    with pytest.raises(RuntimeError, match="Remote branch topic/empty not found"):
        call()
    assert not destination.exists()


def test_partial_nonempty_reuses_single_discovery_for_selected_fetch(tmp_path, monkeypatch) -> None:
    discovery = _ordinary_discovery()
    calls = []

    monkeypatch.setattr(
        clone_partial,
        "discover_clone_refs_with_unborn",
        lambda url, *, server_options=(): (
            calls.append(("discover", url, tuple(server_options))) or discovery
        ),
    )

    class StopFetch(RuntimeError):
        pass

    def stop(client, selected, *, haves=(), filter_spec):
        calls.append(
            (
                "fetch",
                selected.capabilities,
                dict(selected.refs.advertisement.refs),
                tuple(haves),
                filter_spec,
            )
        )
        raise StopFetch("stop after selection")

    monkeypatch.setattr(clone_partial, "fetch_filtered_discovered_clone", stop)

    with pytest.raises(StopFetch):
        clone_partial.clone_partial_repository(
            URL,
            str(tmp_path / "clone"),
            filter_spec="blob:none",
            branch_name="dev",
            single_branch=True,
            server_options=("one",),
        )

    assert calls == [
        ("discover", URL, ("one",)),
        (
            "fetch",
            discovery.capabilities,
            {"refs/heads/dev": DEV_NATIVE},
            (),
            "blob:none",
        ),
    ]


def test_shallow_nonempty_reuses_single_discovery_for_selected_fetch(tmp_path, monkeypatch) -> None:
    discovery = _ordinary_discovery()
    calls = []

    monkeypatch.setattr(
        clone_shallow,
        "discover_clone_refs_with_unborn",
        lambda url, *, server_options=(): (
            calls.append(("discover", url, tuple(server_options))) or discovery
        ),
    )

    class StopFetch(RuntimeError):
        pass

    def stop(client, selected, *, haves=(), deepen=None):
        calls.append(
            (
                "fetch",
                selected.capabilities,
                dict(selected.refs.advertisement.refs),
                tuple(haves),
                deepen,
            )
        )
        raise StopFetch("stop after selection")

    monkeypatch.setattr(clone_shallow, "fetch_discovered_clone", stop)

    with pytest.raises(StopFetch):
        clone_shallow.clone_shallow_repository(
            URL,
            str(tmp_path / "clone"),
            depth=4,
            branch_name="dev",
            single_branch=True,
            server_options=("one",),
        )

    assert calls == [
        ("discover", URL, ("one",)),
        (
            "fetch",
            discovery.capabilities,
            {"refs/heads/dev": DEV_NATIVE},
            (),
            4,
        ),
    ]


def test_discovered_fetch_adapter_does_not_rediscover_capabilities(monkeypatch) -> None:
    discovery = _ordinary_discovery()
    posted = []

    class Client:
        server_options = ()

        def discover_capabilities(self):
            raise AssertionError("known clone capabilities must be reused")

        @staticmethod
        def _wants(advertisement):
            return [MAIN_NATIVE]

        def _post_fetch(self, body):
            posted.append(body)
            return ProtocolV2FetchResponse(
                acknowledgments=(),
                ready=False,
                nak=False,
                shallow=(),
                unshallow=(),
                wanted_refs={},
                pack=b"PACK-test",
            )

    class Parser:
        def __init__(self, data):
            assert data == b"PACK-test"

        def parse(self):
            return {}

    monkeypatch.setattr(clone_v2_reuse, "PackParser", Parser)
    result = clone_v2_reuse.fetch_discovered_clone(Client(), discovery)

    assert result.advertisement is discovery.refs.advertisement
    assert result.objects == {}
    assert len(posted) == 1
    assert b"want " + MAIN_NATIVE.encode() in posted[0]


def test_filtered_discovered_adapter_does_not_rediscover_capabilities(monkeypatch) -> None:
    discovery = _ordinary_discovery()
    posted = []

    class Client:
        server_options = ()

        def discover_capabilities(self):
            raise AssertionError("known clone capabilities must be reused")

        @staticmethod
        def _wants(advertisement):
            return [MAIN_NATIVE]

        def _post_fetch(self, body):
            posted.append(body)
            return ProtocolV2FetchResponse(
                acknowledgments=(),
                ready=False,
                nak=False,
                shallow=(),
                unshallow=(),
                wanted_refs={},
                pack=b"PACK-test",
            )

    class Parser:
        def __init__(self, data):
            assert data == b"PACK-test"

        def parse(self):
            return {}

    monkeypatch.setattr(clone_v2_reuse, "PackParser", Parser)
    result = clone_v2_reuse.fetch_filtered_discovered_clone(
        Client(),
        discovery,
        filter_spec="blob:none",
    )

    assert result.objects == {}
    assert len(posted) == 1
    assert b"filter blob:none" in posted[0]


@pytest.mark.parametrize("kind", ["partial", "shallow"])
def test_programmatic_v0_fallback_keeps_existing_v2_requirement(kind, tmp_path, monkeypatch) -> None:
    if kind == "partial":
        monkeypatch.setattr(
            clone_partial,
            "discover_clone_refs_with_unborn",
            lambda *a, **k: None,
        )
        with pytest.raises(RuntimeError, match="partial clone requires protocol version 2"):
            clone_partial.clone_partial_repository(
                URL,
                str(tmp_path / "partial"),
                filter_spec="blob:none",
                branch_name=None,
                single_branch=False,
            )
    else:
        monkeypatch.setattr(
            clone_shallow,
            "discover_clone_refs_with_unborn",
            lambda *a, **k: None,
        )
        with pytest.raises(RuntimeError, match="shallow clone requires protocol version 2"):
            clone_shallow.clone_shallow_repository(
                URL,
                str(tmp_path / "shallow"),
                depth=1,
                branch_name=None,
                single_branch=True,
            )


def test_cli_delegates_partial_unborn_discovery_to_production_api(tmp_path, monkeypatch, capsys) -> None:
    discovery = _unborn_discovery("main")

    def bomb_preflight(*args, **kwargs):
        raise AssertionError("CLI must not duplicate partial unborn discovery")

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", bomb_preflight)
    monkeypatch.setattr(
        clone_partial,
        "discover_clone_refs_with_unborn",
        lambda *a, **k: discovery,
    )

    destination = tmp_path / "partial"
    assert clone_cli.run_clone(
        ["--filter=blob:none", URL, str(destination)]
    ) == 0

    repo = Repository(str(destination))
    assert repo.refs.resolve_head() is None
    assert "cloned an empty repository" in capsys.readouterr().err.lower()


def test_cli_delegates_shallow_unborn_discovery_to_production_api(tmp_path, monkeypatch, capsys) -> None:
    discovery = _unborn_discovery("main")

    def bomb_preflight(*args, **kwargs):
        raise AssertionError("CLI must not duplicate shallow unborn discovery")

    monkeypatch.setattr(clone_cli, "try_clone_explicit_unborn_remote", bomb_preflight)
    monkeypatch.setattr(
        clone_shallow,
        "discover_clone_refs_with_unborn",
        lambda *a, **k: discovery,
    )

    destination = tmp_path / "shallow"
    assert clone_cli.run_clone(["--depth=1", URL, str(destination)]) == 0

    repo = Repository(str(destination))
    assert repo.refs.resolve_head() is None
    assert not (repo.pygit_dir / "shallow").exists()
    assert "cloned an empty repository" in capsys.readouterr().err.lower()


def test_cli_keeps_legacy_unborn_preflight_only_for_repository_clone_path() -> None:
    assert clone_cli._empty_clone_preflight_available(
        filter_spec="blob:none",
        depth=None,
        server_options=(),
    ) is False
    assert clone_cli._empty_clone_preflight_available(
        filter_spec=None,
        depth=1,
        server_options=(),
    ) is False
    assert clone_cli._empty_clone_preflight_available(
        filter_spec=None,
        depth=None,
        server_options=(),
    ) is True


def _git_config(path: Path, key: str):
    completed = subprocess.run(
        ["git", "-C", str(path), "config", "--get", key],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def test_native_git_empty_partial_and_shallow_clone_baseline(tmp_path) -> None:
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
    subprocess.run(
        ["git", "-C", str(remote), "config", "uploadpack.allowFilter", "true"],
        check=True,
    )

    partial = tmp_path / "partial"
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--filter=blob:none", f"file://{remote}", str(partial)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    subprocess.run(
        ["git", "clone", "--depth=1", f"file://{remote}", str(shallow)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    for path in (partial, shallow):
        assert (path / ".git" / "HEAD").read_text().strip() == (
            "ref: refs/heads/topic/empty"
        )
        shown = subprocess.run(
            ["git", "-C", str(path), "show-ref"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert shown.stdout == ""
        assert not (path / ".git" / "shallow").exists()
        assert _git_config(path, "branch.topic/empty.remote") == "origin"
        assert _git_config(path, "branch.topic/empty.merge") == "refs/heads/topic/empty"

    assert _git_config(partial, "remote.origin.promisor") == "true"
    assert _git_config(partial, "remote.origin.partialclonefilter") == "blob:none"
    assert _git_config(partial, "remote.origin.fetch") == (
        "+refs/heads/*:refs/remotes/origin/*"
    )
    assert _git_config(shallow, "remote.origin.fetch") is None
