from pathlib import Path
from types import SimpleNamespace

import pytest

from pygit.repo import Repository
import pygit.clone_partial as partial
import pygit.clone_shallow as shallow


URL = "https://example.test/empty.git"


def _repo(tmp_path: Path, name: str) -> Repository:
    return Repository.init(str(tmp_path / name))


def test_partial_library_api_short_circuits_explicit_unborn_remote(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "partial-empty")
    seen = {}

    def fake_unborn(url, path, **kwargs):
        seen["url"] = url
        seen["path"] = path
        seen.update(kwargs)
        return SimpleNamespace(repo=repo, branch="topic/empty")

    monkeypatch.setattr(partial, "try_clone_explicit_unborn_remote", fake_unborn)

    result = partial.clone_partial_repository(
        URL,
        str(tmp_path / "unused"),
        filter_spec="blob:none",
        branch_name=None,
        single_branch=True,
        server_options=("trace=1",),
        checkout=False,
    )

    assert result is repo
    assert seen == {
        "url": URL,
        "path": str(tmp_path / "unused"),
        "branch_name": None,
        "single_branch": True,
        "server_options": ("trace=1",),
        "filter_spec": "blob:none",
    }


def test_shallow_library_api_short_circuits_explicit_unborn_remote(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "shallow-empty")
    seen = {}

    def fake_unborn(url, path, **kwargs):
        seen["url"] = url
        seen["path"] = path
        seen.update(kwargs)
        return SimpleNamespace(repo=repo, branch="topic/empty")

    monkeypatch.setattr(shallow, "try_clone_explicit_unborn_remote", fake_unborn)

    result = shallow.clone_shallow_repository(
        URL,
        str(tmp_path / "unused"),
        depth=7,
        branch_name=None,
        single_branch=False,
        server_options=("trace=1",),
        checkout=False,
    )

    assert result is repo
    assert seen == {
        "url": URL,
        "path": str(tmp_path / "unused"),
        "branch_name": None,
        "single_branch": False,
        "server_options": ("trace=1",),
        "depth": 7,
    }


def test_partial_invalid_filter_fails_before_unborn_preflight(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("unborn preflight must not run for an invalid filter")

    monkeypatch.setattr(partial, "try_clone_explicit_unborn_remote", forbidden)

    with pytest.raises((RuntimeError, ValueError), match="filter"):
        partial.clone_partial_repository(
            URL,
            str(tmp_path / "repo"),
            filter_spec="not-a-filter",
            branch_name=None,
            single_branch=False,
        )


def test_shallow_invalid_depth_fails_before_unborn_preflight(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("unborn preflight must not run for invalid depth")

    monkeypatch.setattr(shallow, "try_clone_explicit_unborn_remote", forbidden)

    with pytest.raises(ValueError, match="positive integer"):
        shallow.clone_shallow_repository(
            URL,
            str(tmp_path / "repo"),
            depth=0,
            branch_name=None,
            single_branch=False,
        )


def test_partial_explicit_branch_failure_from_unborn_path_is_not_fallback(monkeypatch, tmp_path):
    def reject_branch(url, path, **kwargs):
        assert kwargs["branch_name"] == "topic/empty"
        raise RuntimeError("Remote branch topic/empty not found in upstream origin")

    monkeypatch.setattr(partial, "try_clone_explicit_unborn_remote", reject_branch)

    with pytest.raises(RuntimeError, match="Remote branch topic/empty not found"):
        partial.clone_partial_repository(
            URL,
            str(tmp_path / "repo"),
            filter_spec="blob:none",
            branch_name="topic/empty",
            single_branch=True,
        )


def test_shallow_explicit_branch_failure_from_unborn_path_is_not_fallback(monkeypatch, tmp_path):
    def reject_branch(url, path, **kwargs):
        assert kwargs["branch_name"] == "topic/empty"
        raise RuntimeError("Remote branch topic/empty not found in upstream origin")

    monkeypatch.setattr(shallow, "try_clone_explicit_unborn_remote", reject_branch)

    with pytest.raises(RuntimeError, match="Remote branch topic/empty not found"):
        shallow.clone_shallow_repository(
            URL,
            str(tmp_path / "repo"),
            depth=1,
            branch_name="topic/empty",
            single_branch=True,
        )


def test_partial_replaced_fetch_client_preserves_old_injection_seam(monkeypatch, tmp_path):
    class InjectedClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("injected partial client reached")

    def forbidden(*args, **kwargs):
        raise AssertionError("hidden unborn preflight bypassed injected client")

    monkeypatch.setattr(partial, "SmartHttpV2FetchClient", InjectedClient)
    monkeypatch.setattr(partial, "try_clone_explicit_unborn_remote", forbidden)

    with pytest.raises(RuntimeError, match="injected partial client reached"):
        partial.clone_partial_repository(
            URL,
            str(tmp_path / "repo"),
            filter_spec="blob:none",
            branch_name=None,
            single_branch=False,
        )


def test_shallow_replaced_fetch_client_preserves_old_injection_seam(monkeypatch, tmp_path):
    class InjectedClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("injected shallow client reached")

    def forbidden(*args, **kwargs):
        raise AssertionError("hidden unborn preflight bypassed injected client")

    monkeypatch.setattr(shallow, "SmartHttpV2FetchClient", InjectedClient)
    monkeypatch.setattr(shallow, "try_clone_explicit_unborn_remote", forbidden)

    with pytest.raises(RuntimeError, match="injected shallow client reached"):
        shallow.clone_shallow_repository(
            URL,
            str(tmp_path / "repo"),
            depth=1,
            branch_name=None,
            single_branch=False,
        )


def test_partial_library_unborn_path_does_not_create_second_client(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "partial-short-circuit")

    def fake_unborn(*args, **kwargs):
        return SimpleNamespace(repo=repo, branch="main")

    monkeypatch.setattr(partial, "try_clone_explicit_unborn_remote", fake_unborn)

    # The original client identity must remain in place so the new preflight is
    # eligible; returning from it must happen before normal client construction.
    assert partial.SmartHttpV2FetchClient is partial._ORIGINAL_SMART_HTTP_V2_FETCH_CLIENT
    result = partial.clone_partial_repository(
        URL,
        str(tmp_path / "unused"),
        filter_spec="blob:none",
        branch_name=None,
        single_branch=False,
    )
    assert result is repo


def test_shallow_library_unborn_path_does_not_create_second_client(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "shallow-short-circuit")

    def fake_unborn(*args, **kwargs):
        return SimpleNamespace(repo=repo, branch="main")

    monkeypatch.setattr(shallow, "try_clone_explicit_unborn_remote", fake_unborn)

    assert shallow.SmartHttpV2FetchClient is shallow._ORIGINAL_SMART_HTTP_V2_FETCH_CLIENT
    result = shallow.clone_shallow_repository(
        URL,
        str(tmp_path / "unused"),
        depth=2,
        branch_name=None,
        single_branch=False,
    )
    assert result is repo
