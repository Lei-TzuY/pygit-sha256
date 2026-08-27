from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_cli import run_push
from pygit.push_defaults import parse_push_refspec, push_default, resolve_push_plan
from pygit.push_transport import push_branch
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


def _set_upstream(repo: Repository, remote: str, branch: str) -> None:
    repo.config_set("branch", "main.remote", remote)
    repo.config_set("branch", "main.merge", f"refs/heads/{branch}")


def test_push_default_defaults_to_simple_and_tracking_aliases_upstream(tmp_path):
    repo = _repo(tmp_path)
    assert push_default(repo) == "simple"
    repo.config_set("push", "default", "tracking")
    assert push_default(repo) == "upstream"


def test_simple_requires_same_named_upstream_on_central_remote(tmp_path):
    repo = _repo(tmp_path)
    _set_upstream(repo, "origin", "release")
    with pytest.raises(RuntimeError, match="does not match current branch"):
        resolve_push_plan(repo, "origin")


def test_simple_behaves_like_current_on_different_push_remote(tmp_path):
    repo = _repo(tmp_path)
    repo.add_remote("publish", "https://example.invalid/publish.git")
    _set_upstream(repo, "origin", "release")
    plan = resolve_push_plan(repo, "publish")
    assert plan.mode == "simple"
    assert [(s.source, s.target) for s in plan.specs] == [("main", "main")]


def test_simple_without_upstream_rejects_central_remote(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="has no upstream"):
        resolve_push_plan(repo, "origin")


def test_current_pushes_same_named_branch_without_upstream(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "default", "current")
    plan = resolve_push_plan(repo, "origin")
    assert [(s.source, s.target) for s in plan.specs] == [("main", "main")]


def test_upstream_targets_configured_merge_branch(tmp_path):
    repo = _repo(tmp_path)
    _set_upstream(repo, "origin", "release")
    repo.config_set("push", "default", "upstream")
    plan = resolve_push_plan(repo, "origin")
    assert [(s.source, s.target) for s in plan.specs] == [("main", "release")]


def test_upstream_refuses_non_upstream_push_remote(tmp_path):
    repo = _repo(tmp_path)
    repo.add_remote("publish", "https://example.invalid/publish.git")
    _set_upstream(repo, "origin", "release")
    repo.config_set("push", "default", "upstream")
    with pytest.raises(RuntimeError, match="requires pushing to upstream remote"):
        resolve_push_plan(repo, "publish")


def test_nothing_requires_explicit_refspec(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "default", "nothing")
    with pytest.raises(RuntimeError, match='push.default is "nothing"'):
        resolve_push_plan(repo, "origin")
    plan = resolve_push_plan(repo, "origin", ["HEAD:release"])
    assert [(s.source, s.target) for s in plan.specs] == [("main", "release")]


def test_explicit_refspec_force_and_remote_push_precedence(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "default", "nothing")
    spec = parse_push_refspec(repo, "+main:release")[0]
    assert (spec.source, spec.target, spec.force) == ("main", "release", True)

    repo.config_set("remote", "origin.push", "main:deploy")
    plan = resolve_push_plan(repo, "origin")
    assert plan.mode == "remote.push"
    assert [(s.source, s.target) for s in plan.specs] == [("main", "deploy")]

    explicit = resolve_push_plan(repo, "origin", ["HEAD:hotfix"])
    assert explicit.mode == "explicit"
    assert [(s.source, s.target) for s in explicit.specs] == [("main", "hotfix")]


def test_matching_uses_local_remote_tracking_intersection(tmp_path):
    repo = _repo(tmp_path)
    repo.branch("topic")
    repo.branch("local-only")
    main_sha = repo.refs.get_branch("main")
    topic_sha = repo.refs.get_branch("topic")
    assert main_sha and topic_sha
    repo.refs.set_remote("origin", "main", main_sha)
    repo.refs.set_remote("origin", "topic", topic_sha)
    repo.refs.set_remote("origin", "remote-only", main_sha)
    repo.config_set("push", "default", "matching")

    plan = resolve_push_plan(repo, "origin")
    assert [(s.source, s.target) for s in plan.specs] == [
        ("main", "main"),
        ("topic", "topic"),
    ]


def test_matching_colon_refspec_uses_same_selection(tmp_path):
    repo = _repo(tmp_path)
    repo.branch("topic")
    sha = repo.refs.get_branch("topic")
    assert sha
    repo.refs.set_remote("origin", "topic", sha)
    plan = resolve_push_plan(repo, "origin", ["+:"])
    assert [(s.source, s.target, s.force) for s in plan.specs] == [
        ("topic", "topic", True)
    ]


def test_push_auto_setup_remote_marks_default_plan(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "default", "current")
    repo.config_set("push", "autoSetupRemote", "true")
    plan = resolve_push_plan(repo, "origin")
    assert plan.auto_setup_upstream is True


def test_run_push_upstream_uses_target_aware_transport(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    _set_upstream(repo, "origin", "release")
    repo.config_set("push", "default", "upstream")
    calls = []

    def fake_push_branch(repo_obj, remote, source, target, *, force=False):
        calls.append((remote, source, target, force))
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
    assert run_push([]) == 0
    assert calls == [("origin", "main", "release", False)]
    assert "main -> origin/release" in capsys.readouterr().out


def test_run_push_auto_setup_persists_tracking(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("push", "default", "current")
    repo.config_set("push", "autoSetupRemote", "true")

    def fake_push(self, remote="origin", force=False):
        return {
            "status": "pushed",
            "remote": remote,
            "branch": self.refs.current_branch(),
            "sha": self.refs.resolve_head(),
            "objects": 1,
        }

    monkeypatch.setattr(Repository, "push", fake_push)
    monkeypatch.chdir(repo.worktree)
    assert run_push([]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.config_get("branch", "main.remote") == "origin"
    assert reopened.config_get("branch", "main.merge") == "refs/heads/main"


def test_run_push_u_tracks_explicit_destination(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    def fake_push_branch(repo_obj, remote, source, target, *, force=False):
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
    assert run_push(["-u", "origin", "HEAD:release"]) == 0
    reopened = Repository(str(repo.worktree))
    assert reopened.config_get("branch", "main.remote") == "origin"
    assert reopened.config_get("branch", "main.merge") == "refs/heads/release"


def test_target_aware_transport_uses_destination_ref(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    source_sha = repo.refs.get_branch("main")
    assert source_sha
    observed = {}

    class FakeClient:
        def __init__(self, url):
            observed["url"] = url

        def discover(self):
            return SimpleNamespace(refs={})

        def push(self, ref_name, new_oid, objects, advertisement=None):
            observed["ref_name"] = ref_name
            observed["new_oid"] = new_oid
            return SimpleNamespace(
                old_oid="0" * 40,
                new_oid=new_oid,
                objects_sent=len(objects),
            )

    class FakeExporter:
        def __init__(self, store, native_map, have_shas=None):
            self.objects = [b"obj"]
            self.converted = {}

        def export_oid(self, oid):
            observed["source_oid"] = oid
            return "a" * 40

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    monkeypatch.setattr("pygit.push_transport.NativeExporter", FakeExporter)
    result = push_branch(repo, "origin", "main", "release")

    assert observed["ref_name"] == "refs/heads/release"
    assert observed["source_oid"] == source_sha
    assert result["branch"] == "release"
    assert repo.refs.get_remote("origin", "release") == source_sha


def test_invalid_push_default_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "default", "surprise")
    with pytest.raises(RuntimeError, match="unsupported push.default"):
        resolve_push_plan(repo, "origin")
