from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_cli import run_push
from pygit.push_defaults import all_branch_specs, all_tag_specs, parse_push_refspec
from pygit.push_transport import delete_remote_ref, push_ref
from pygit.repo import Repository


def _commit(repo: Repository, name: str = "a.txt", text: str = "one") -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.add_remote("origin", "https://example.invalid/origin.git")
    return repo


def test_empty_source_refspec_deletes_branch(tmp_path):
    repo = _repo(tmp_path)
    spec = parse_push_refspec(repo, ":old")[0]
    assert spec.delete is True
    assert spec.source == ""
    assert spec.target_ref == "refs/heads/old"


def test_fully_qualified_tag_refspec_is_supported(tmp_path):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("v1.0", sha)
    spec = parse_push_refspec(repo, "refs/tags/v1.0")[0]
    assert (spec.namespace, spec.source, spec.target) == ("tags", "v1.0", "v1.0")
    assert spec.source_ref == spec.target_ref == "refs/tags/v1.0"


def test_unqualified_destination_inherits_tag_namespace(tmp_path):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("v1", sha)
    spec = parse_push_refspec(repo, "refs/tags/v1:release")[0]
    assert spec.namespace == "tags"
    assert spec.target_ref == "refs/tags/release"


def test_branch_wildcard_maps_capture_into_destination(tmp_path):
    repo = _repo(tmp_path)
    repo.branch("feature/a")
    repo.branch("feature/b")
    specs = parse_push_refspec(repo, "refs/heads/feature/*:refs/heads/archive/*")
    assert [(s.source, s.target) for s in specs] == [
        ("feature/a", "archive/a"),
        ("feature/b", "archive/b"),
    ]


def test_tag_wildcard_expands_local_tags(tmp_path):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("release/1", sha)
    repo.refs.set_tag("release/2", sha)
    specs = parse_push_refspec(repo, "refs/tags/release/*:refs/tags/archive/*")
    assert [(s.namespace, s.source, s.target) for s in specs] == [
        ("tags", "release/1", "archive/1"),
        ("tags", "release/2", "archive/2"),
    ]


def test_wildcard_requires_one_star_on_each_side(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="one '\\*' on each side"):
        parse_push_refspec(repo, "refs/heads/*:refs/heads/release")
    with pytest.raises(RuntimeError, match="one '\\*' on each side"):
        parse_push_refspec(repo, "refs/heads/**:refs/heads/*")


def test_all_branch_and_tag_specs_are_deterministic(tmp_path):
    repo = _repo(tmp_path)
    repo.branch("zeta")
    repo.branch("alpha")
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("v2", sha)
    repo.refs.set_tag("v1", sha)
    assert [s.source for s in all_branch_specs(repo)] == ["alpha", "main", "zeta"]
    assert [s.source for s in all_tag_specs(repo)] == ["v1", "v2"]


def test_run_push_all_uses_each_local_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")
    calls = []

    def fake_push_branch(repo_obj, remote, source, target, *, force=False):
        calls.append((source, target, force))
        return {
            "status": "pushed",
            "remote": remote,
            "source_branch": source,
            "branch": target,
            "sha": repo_obj.refs.get_branch(source),
            "objects": 1,
        }

    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--all", "origin"]) == 0
    assert calls == [("main", "main", False), ("topic", "topic", False)]


def test_run_push_tags_does_not_require_attached_head_with_explicit_remote(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("v1", sha)
    repo.refs.set_head_detached(sha)
    calls = []

    def fake_push_ref(repo_obj, remote, source_ref, target_ref, *, force=False):
        calls.append((source_ref, target_ref, force))
        return {"status": "pushed", "remote": remote, "ref": target_ref, "sha": sha, "objects": 1}

    monkeypatch.setattr("pygit.push_cli.push_ref", fake_push_ref)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--tags", "origin"]) == 0
    assert calls == [("refs/tags/v1", "refs/tags/v1", False)]


def test_delete_transport_sends_zero_oid_and_removes_tracking_ref(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_remote("origin", "old", sha)
    observed = {}

    class FakeClient:
        def __init__(self, url):
            observed["url"] = url

        def discover(self):
            return SimpleNamespace(refs={"refs/heads/old": "a" * 40})

        def push(self, ref_name, new_oid, objects, advertisement=None):
            observed.update(ref_name=ref_name, new_oid=new_oid, objects=list(objects))
            return SimpleNamespace(old_oid="a" * 40, new_oid=new_oid, objects_sent=0)

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    result = delete_remote_ref(repo, "origin", "refs/heads/old")
    assert result["status"] == "deleted"
    assert observed["ref_name"] == "refs/heads/old"
    assert observed["new_oid"] == "0" * 40
    assert observed["objects"] == []
    assert repo.refs.get_remote("origin", "old") is None


def test_existing_remote_tag_requires_force(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("v1", sha)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return SimpleNamespace(refs={"refs/tags/v1": "a" * 40})

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="remote tag already exists"):
        push_ref(repo, "origin", "refs/tags/v1", "refs/tags/v1")
