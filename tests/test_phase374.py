from __future__ import annotations

import os
from pathlib import Path

import pytest

import pygit.clone_origin as clone_origin
from pygit.clone_origin import retarget_completed_clone_remote
from pygit.promisor import read_promisor_state, update_promisor_state
from pygit.repo import Repository


URL = "https://example.invalid/repo.git"
NATIVE_OID = "1" * 40
LOCAL_OID = "a" * 64


def _repo(tmp_path: Path, *, promisor: bool = False) -> Repository:
    repo = Repository.init(str(tmp_path))
    repo.add_remote("origin", URL)
    repo.refs.set_remote("origin", "main", LOCAL_OID)
    repo.refs.set_branch("main", LOCAL_OID)
    repo.refs.set_head_symbolic("main")
    repo._write_native_map({LOCAL_OID: NATIVE_OID}, "origin")

    config = repo._read_config()
    config.setdefault("remotes", {}).setdefault("origin", {})["default_branch"] = "main"
    repo._write_config(config)

    repo.config_set("remote", "origin.url", URL)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo.config_set("branch", "main.remote", "origin")
    repo.config_set("branch", "main.merge", "refs/heads/main")

    if promisor:
        repo.config_set("extensions", "partialClone", "origin")
        repo.config_set("remote", "origin.promisor", "true")
        repo.config_set("remote", "origin.partialCloneFilter", "blob:none")
        update_promisor_state(
            repo.pygit_dir,
            remote="origin",
            filter_spec="blob:none",
            promised={NATIVE_OID: "blob"},
        )
    return repo


def _tree_snapshot(root: Path):
    if root.is_symlink():
        return ("symlink", os.readlink(root))
    if root.is_file():
        return ("file", root.read_bytes(), root.stat().st_mode & 0o7777)
    if not root.exists():
        return None
    result = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            result.append((rel, "symlink", os.readlink(path)))
        elif path.is_dir():
            result.append((rel, "dir"))
        else:
            result.append((rel, "file", path.read_bytes(), path.stat().st_mode & 0o7777))
    return ("dir", tuple(result))


def _retarget_surface(repo: Repository):
    return {
        relative: _tree_snapshot(repo.pygit_dir / relative)
        for relative in clone_origin._RETARGET_METADATA_ROOTS
    }


def _object_surface(repo: Repository):
    return _tree_snapshot(repo.pygit_dir / "objects")


def test_success_retargets_all_clone_metadata_without_touching_objects(tmp_path) -> None:
    repo = _repo(tmp_path, promisor=True)
    objects_before = _object_surface(repo)

    retarget_completed_clone_remote(repo, "upstream")

    assert repo.list_remotes() == {"upstream": URL}
    assert repo.refs.get_remote("origin", "main") is None
    assert repo.refs.get_remote("upstream", "main") == LOCAL_OID
    assert repo._read_native_map("origin") == {}
    assert repo._read_native_map("upstream") == {LOCAL_OID: NATIVE_OID}
    assert repo.config_get("remote", "origin.url") is None
    assert repo.config_get("remote", "upstream.url") == URL
    assert repo.config_get("branch", "main.remote") == "upstream"
    assert repo.config_get("extensions", "partialClone") == "upstream"
    assert read_promisor_state(repo.pygit_dir)["remotes"] == {
        "upstream": {"filter": "blob:none"}
    }
    assert _object_surface(repo) == objects_before


def test_failure_after_namespace_rename_restores_exact_metadata(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path, promisor=True)
    before = _retarget_surface(repo)
    objects_before = _object_surface(repo)

    monkeypatch.setattr(
        clone_origin,
        "_retarget_promisor_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("promisor write failed")),
    )

    with pytest.raises(OSError, match="promisor write failed"):
        retarget_completed_clone_remote(repo, "upstream")

    assert _retarget_surface(repo) == before
    assert _object_surface(repo) == objects_before
    assert repo.list_remotes() == {"origin": URL}
    assert repo.refs.get_remote("origin", "main") == LOCAL_OID
    assert repo.refs.get_remote("upstream", "main") is None


def test_failure_after_promisor_move_restores_exact_metadata(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path, promisor=True)
    before = _retarget_surface(repo)
    objects_before = _object_surface(repo)
    real_config_set = Repository.config_set

    def fail_partial_clone_config(self, section, key, value):
        if section == "extensions" and key == "partialClone" and value == "upstream":
            raise OSError("config write failed")
        return real_config_set(self, section, key, value)

    monkeypatch.setattr(Repository, "config_set", fail_partial_clone_config)

    with pytest.raises(OSError, match="config write failed"):
        retarget_completed_clone_remote(repo, "upstream")

    assert _retarget_surface(repo) == before
    assert _object_surface(repo) == objects_before
    assert read_promisor_state(repo.pygit_dir)["remotes"] == {
        "origin": {"filter": "blob:none"}
    }
    assert repo.config_get("extensions", "partialClone") == "origin"


def test_failure_after_generic_rename_returns_to_origin(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    before = _retarget_surface(repo)
    real_rename = clone_origin.rename_remote

    def rename_then_fail(repo_arg, old, new):
        real_rename(repo_arg, old, new)
        raise RuntimeError("post-rename failure")

    monkeypatch.setattr(clone_origin, "rename_remote", rename_then_fail)

    with pytest.raises(RuntimeError, match="post-rename failure"):
        retarget_completed_clone_remote(repo, "upstream")

    assert _retarget_surface(repo) == before
    assert repo.list_remotes() == {"origin": URL}
    assert repo._read_native_map("origin") == {LOCAL_OID: NATIVE_OID}


def test_old_equals_new_is_a_zero_mutation_fast_path(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    before = _retarget_surface(repo)

    monkeypatch.setattr(
        clone_origin,
        "_snapshot_retarget_metadata",
        lambda repo_arg: pytest.fail("identity retarget must not snapshot metadata"),
    )

    retarget_completed_clone_remote(repo, "origin")
    assert _retarget_surface(repo) == before


def test_promisor_collision_fails_before_snapshot_or_mutation(tmp_path, monkeypatch) -> None:
    repo = _repo(tmp_path, promisor=True)
    update_promisor_state(
        repo.pygit_dir,
        remote="upstream",
        filter_spec="blob:none",
        promised={},
    )
    before = _retarget_surface(repo)

    monkeypatch.setattr(
        clone_origin,
        "_snapshot_retarget_metadata",
        lambda repo_arg: pytest.fail("promisor collision must fail before snapshot"),
    )

    with pytest.raises(RuntimeError, match="Promisor remote already exists"):
        retarget_completed_clone_remote(repo, "upstream")

    assert _retarget_surface(repo) == before


@pytest.mark.skipif(os.name == "nt", reason="symlink permissions are platform-dependent")
def test_symlinked_retarget_metadata_is_rejected_before_mutation(tmp_path) -> None:
    repo = _repo(tmp_path)
    config_path = repo.pygit_dir / "config"
    saved = config_path.read_bytes()
    backup = repo.pygit_dir / "config.real"
    backup.write_bytes(saved)
    config_path.unlink()
    config_path.symlink_to(backup.name)

    with pytest.raises(RuntimeError, match="refuses symlinked metadata"):
        retarget_completed_clone_remote(repo, "upstream")

    assert config_path.is_symlink()
    assert backup.read_bytes() == saved
    assert repo.list_remotes() == {"origin": URL}


def test_rollback_failure_is_reported_without_masking_that_retarget_failed(
    tmp_path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)

    monkeypatch.setattr(
        clone_origin,
        "rename_remote",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("rename failed")),
    )
    monkeypatch.setattr(
        clone_origin,
        "_restore_retarget_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("rollback failed")),
    )

    with pytest.raises(
        RuntimeError,
        match="clone remote retarget failed and metadata rollback also failed: rename failed",
    ):
        retarget_completed_clone_remote(repo, "upstream")
