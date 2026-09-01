"""Phase400 explicit tag-clone regressions."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pygit.clone_tag import try_clone_explicit_tag_remote
from pygit.objects import TagObject
from pygit.remote import Advertisement, NativeObject


def _oid(kind: str, data: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()


def _native_graph(*, annotated: bool = False, tag_target_type: str = "commit"):
    blob_data = b"hello from tag clone\n"
    blob_oid = _oid("blob", blob_data)
    tree_data = b"100644 f.txt\0" + bytes.fromhex(blob_oid)
    tree_oid = _oid("tree", tree_data)
    commit_data = (
        f"tree {tree_oid}\n"
        "author Tester <test@example.com> 0 +0000\n"
        "committer Tester <test@example.com> 0 +0000\n"
        "\n"
        "tag clone target\n"
    ).encode()
    commit_oid = _oid("commit", commit_data)

    objects = {
        blob_oid: NativeObject("blob", blob_data, blob_oid),
        tree_oid: NativeObject("tree", tree_data, tree_oid),
        commit_oid: NativeObject("commit", commit_data, commit_oid),
    }
    if not annotated:
        return commit_oid, commit_oid, objects

    target_oid = commit_oid
    if tag_target_type == "blob":
        target_oid = blob_oid
    tag_data = (
        f"object {target_oid}\n"
        f"type {tag_target_type}\n"
        "tag release\n"
        "tagger Tester <test@example.com> 0 +0000\n"
        "\n"
        "annotated release\n"
    ).encode()
    tag_oid = _oid("tag", tag_data)
    objects[tag_oid] = NativeObject("tag", tag_data, tag_oid)
    return tag_oid, target_oid, objects


def _install_client(monkeypatch, advertisement, objects):
    calls = []

    class Client:
        def __init__(self, url, server_options=()):
            assert url == "https://example.test/repo.git"
            self.server_options = tuple(server_options)

        def discover_refs(self):
            calls.append(("discover", None))
            return advertisement

        def fetch(self, haves=(), advertisement=None):
            calls.append(("fetch", dict(advertisement.refs)))
            return SimpleNamespace(objects=objects, shallow=(), unshallow=())

    monkeypatch.setattr("pygit.clone_tag.SmartHttpV2FetchClient", Client)
    return calls


def test_lightweight_tag_clone_detaches_head_and_keeps_remote_branches(tmp_path, monkeypatch):
    tag_oid, commit_oid, objects = _native_graph()
    advertisement = Advertisement(
        {
            "refs/heads/main": commit_oid,
            "refs/tags/release": tag_oid,
        },
        {"version 2", "fetch"},
        {"HEAD": "refs/heads/main"},
    )
    _install_client(monkeypatch, advertisement, objects)

    result = try_clone_explicit_tag_remote(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        branch_name="release",
        single_branch=False,
    )
    assert result is not None
    repo = result.repo
    assert repo.refs.is_detached() is True
    assert repo.refs.current_branch() is None
    assert repo.refs.resolve_head() == result.commit_oid
    assert repo.refs.get_tag("release") == result.commit_oid
    assert repo.refs.get_remote("origin", "main") == result.commit_oid
    assert repo.refs.get_remote_head("origin") == "main"
    assert repo.config_get("remote", "origin.fetch") == "+refs/heads/*:refs/remotes/origin/*"
    assert repo.config_get("branch", "release.remote") is None
    assert (repo.worktree / "f.txt").read_bytes() == b"hello from tag clone\n"
    assert repo.refs.read_reflog("HEAD")[0].new_sha == result.commit_oid


def test_annotated_tag_clone_preserves_tag_object_and_peels_head(tmp_path, monkeypatch):
    tag_oid, commit_oid, objects = _native_graph(annotated=True)
    advertisement = Advertisement(
        {
            "refs/heads/main": commit_oid,
            "refs/tags/release": tag_oid,
            "refs/tags/release^{}": commit_oid,
        },
        {"version 2", "fetch"},
        {"HEAD": "refs/heads/main"},
    )
    _install_client(monkeypatch, advertisement, objects)

    result = try_clone_explicit_tag_remote(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        branch_name="release",
        single_branch=False,
    )
    assert result is not None
    local_tag_oid = result.repo.refs.get_tag("release")
    assert local_tag_oid is not None
    assert local_tag_oid != result.commit_oid
    tag = result.repo.store.read(local_tag_oid)
    assert isinstance(tag, TagObject)
    assert tag.target_sha == result.commit_oid
    assert result.repo.refs.resolve_head() == result.commit_oid


def test_branch_name_wins_over_same_named_tag(tmp_path, monkeypatch):
    tag_oid, commit_oid, objects = _native_graph(annotated=True)
    advertisement = Advertisement(
        {
            "refs/heads/release": commit_oid,
            "refs/tags/release": tag_oid,
            "refs/tags/release^{}": commit_oid,
        },
        {"version 2", "fetch"},
        {},
    )
    calls = _install_client(monkeypatch, advertisement, objects)
    result = try_clone_explicit_tag_remote(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        branch_name="release",
        single_branch=False,
    )
    assert result is None
    assert calls == [("discover", None)]
    assert not (tmp_path / "clone").exists()


def test_single_branch_tag_clone_uses_tag_refspec_without_origin_head(tmp_path, monkeypatch):
    tag_oid, commit_oid, objects = _native_graph(annotated=True)
    advertisement = Advertisement(
        {
            "refs/heads/main": commit_oid,
            "refs/tags/release": tag_oid,
            "refs/tags/release^{}": commit_oid,
        },
        {"version 2", "fetch"},
        {"HEAD": "refs/heads/main"},
    )
    calls = _install_client(monkeypatch, advertisement, objects)
    result = try_clone_explicit_tag_remote(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        branch_name="release",
        single_branch=True,
    )
    assert result is not None
    assert result.repo.refs.list_remotes("origin") == []
    assert result.repo.refs.get_remote_head("origin") is None
    assert result.repo.config_get("remote", "origin.fetch") == "+refs/tags/release:refs/tags/release"
    assert calls[1][0] == "fetch"
    assert calls[1][1] == {
        "refs/tags/release": tag_oid,
        "refs/tags/release^{}": commit_oid,
    }


def test_tag_clone_no_checkout_leaves_worktree_unpopulated(tmp_path, monkeypatch):
    tag_oid, commit_oid, objects = _native_graph()
    advertisement = Advertisement(
        {"refs/tags/release": tag_oid},
        {"version 2", "fetch"},
        {},
    )
    _install_client(monkeypatch, advertisement, objects)
    result = try_clone_explicit_tag_remote(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        branch_name="release",
        single_branch=True,
        checkout=False,
    )
    assert result is not None
    assert not (result.repo.worktree / "f.txt").exists()
    assert result.repo.refs.resolve_head() == result.commit_oid


def test_tag_that_does_not_peel_to_commit_fails_and_cleans_destination(tmp_path, monkeypatch):
    tag_oid, blob_oid, objects = _native_graph(annotated=True, tag_target_type="blob")
    advertisement = Advertisement(
        {
            "refs/tags/release": tag_oid,
            "refs/tags/release^{}": blob_oid,
        },
        {"version 2", "fetch"},
        {},
    )
    _install_client(monkeypatch, advertisement, objects)
    destination = tmp_path / "clone"
    with pytest.raises(RuntimeError, match="peel to a commit"):
        try_clone_explicit_tag_remote(
            "https://example.test/repo.git",
            str(destination),
            branch_name="release",
            single_branch=True,
        )
    assert not destination.exists()


def test_protocol_v0_or_unknown_tag_falls_back_without_local_mutation(tmp_path, monkeypatch):
    class V0:
        def __init__(self, *args, **kwargs):
            pass

        def discover_refs(self):
            return None

    monkeypatch.setattr("pygit.clone_tag.SmartHttpV2FetchClient", V0)
    assert try_clone_explicit_tag_remote(
        "https://example.test/repo.git",
        str(tmp_path / "clone"),
        branch_name="release",
        single_branch=False,
    ) is None
    assert not (tmp_path / "clone").exists()


def test_native_git_clone_branch_tag_is_detached_and_annotated_tag_is_preserved(tmp_path):
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "--object-format=sha256"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    (source / "f.txt").write_text("native tag clone\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=source, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=source, check=True)
    subprocess.run(["git", "tag", "-a", "release", "-m", "release"], cwd=source, check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
    subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    subprocess.run(["git", "clone", "-q", "-b", "release", str(remote), str(clone)], check=True)

    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=clone,
        text=True,
        capture_output=True,
    )
    assert symbolic.returncode != 0
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=clone, text=True).strip()
    peeled = subprocess.check_output(
        ["git", "rev-parse", "refs/tags/release^{}"],
        cwd=clone,
        text=True,
    ).strip()
    tag_object = subprocess.check_output(
        ["git", "rev-parse", "refs/tags/release"],
        cwd=clone,
        text=True,
    ).strip()
    assert head == peeled
    assert tag_object != peeled
    assert subprocess.check_output(
        ["git", "config", "--get", "remote.origin.fetch"],
        cwd=clone,
        text=True,
    ).strip() == "+refs/heads/*:refs/remotes/origin/*"
    branch_config = subprocess.run(
        ["git", "config", "--get-regexp", r"^branch\."],
        cwd=clone,
        text=True,
        capture_output=True,
    )
    assert branch_config.returncode != 0
