from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_cli import run_push
from pygit.push_defaults import PushPlan, PushSpec, resolve_push_plan
from pygit.push_prune import prune_specs
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


def test_negative_pattern_filters_positive_branch_pattern(tmp_path):
    repo = _repo(tmp_path)
    repo.branch("feature/a")
    repo.branch("feature/private-a")
    repo.branch("feature/b")
    plan = resolve_push_plan(
        repo,
        "origin",
        (
            "refs/heads/feature/*:refs/heads/archive/*",
            "^refs/heads/feature/private-*",
        ),
    )
    assert [(s.source, s.target) for s in plan.specs] == [
        ("feature/a", "archive/a"),
        ("feature/b", "archive/b"),
    ]


def test_negative_exact_ref_filters_one_positive(tmp_path):
    repo = _repo(tmp_path)
    repo.branch("keep")
    repo.branch("skip")
    plan = resolve_push_plan(
        repo,
        "origin",
        ("refs/heads/*:refs/heads/*", "^refs/heads/skip"),
    )
    assert "skip" not in [spec.source for spec in plan.specs]
    assert "keep" in [spec.source for spec in plan.specs]


def test_negative_refspec_requires_positive_refspec(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="requires at least one positive"):
        resolve_push_plan(repo, "origin", ("^refs/heads/dev-*",))


def test_negative_refspec_rejects_destination(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="only a source ref"):
        resolve_push_plan(
            repo,
            "origin",
            ("refs/heads/*:refs/heads/*", "^refs/heads/dev-*:refs/heads/dev-*"),
        )


def test_negative_refspec_rejects_raw_object_id(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="raw object IDs"):
        resolve_push_plan(
            repo,
            "origin",
            ("refs/heads/*:refs/heads/*", "^" + "a" * 40),
        )


def test_prune_same_name_branch_pattern_deletes_missing_remote(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("keep")

    class FakeClient:
        def __init__(self, url):
            assert url.endswith("origin.git")

        def discover(self):
            return SimpleNamespace(
                refs={
                    "refs/heads/main": "a" * 40,
                    "refs/heads/keep": "b" * 40,
                    "refs/heads/stale": "c" * 40,
                }
            )

    monkeypatch.setattr("pygit.push_prune.SmartHttpPushClient", FakeClient)
    plan = resolve_push_plan(repo, "origin", ("refs/heads/*:refs/heads/*",))
    deletions = prune_specs(
        repo,
        "origin",
        plan,
        ("refs/heads/*:refs/heads/*",),
    )
    assert [(spec.namespace, spec.target) for spec in deletions] == [("heads", "stale")]


def test_prune_custom_mapping_uses_reverse_capture(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("feature/a")

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return SimpleNamespace(
                refs={
                    "refs/heads/archive/a": "a" * 40,
                    "refs/heads/archive/stale": "b" * 40,
                    "refs/heads/unrelated": "c" * 40,
                }
            )

    monkeypatch.setattr("pygit.push_prune.SmartHttpPushClient", FakeClient)
    raw = ("refs/heads/feature/*:refs/heads/archive/*",)
    plan = resolve_push_plan(repo, "origin", raw)
    deletions = prune_specs(repo, "origin", plan, raw)
    assert [spec.target for spec in deletions] == ["archive/stale"]


def test_negative_refspec_protects_remote_ref_from_prune(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return SimpleNamespace(
                refs={
                    "refs/heads/public-old": "a" * 40,
                    "refs/heads/private-old": "b" * 40,
                }
            )

    monkeypatch.setattr("pygit.push_prune.SmartHttpPushClient", FakeClient)
    raw = (
        "refs/heads/*:refs/heads/*",
        "^refs/heads/private-*",
    )
    plan = resolve_push_plan(repo, "origin", raw)
    deletions = prune_specs(repo, "origin", plan, raw)
    assert [spec.target for spec in deletions] == ["public-old"]


def test_prune_tags_deletes_remote_tag_missing_locally(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    sha = repo.refs.resolve_head()
    assert sha
    repo.refs.set_tag("v1", sha)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return SimpleNamespace(
                refs={
                    "refs/tags/v1": "a" * 40,
                    "refs/tags/stale": "b" * 40,
                    "refs/heads/main": "c" * 40,
                }
            )

    monkeypatch.setattr("pygit.push_prune.SmartHttpPushClient", FakeClient)
    plan = PushPlan("origin", (), "tags")
    deletions = prune_specs(repo, "origin", plan, (), tags=True)
    assert [(spec.namespace, spec.target) for spec in deletions] == [("tags", "stale")]


def test_exact_refspec_does_not_prune_unrelated_remote_refs(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    plan = resolve_push_plan(repo, "origin", ("main",))

    class BombClient:
        def __init__(self, url):
            raise AssertionError("exact refspec prune should not need remote discovery")

    monkeypatch.setattr("pygit.push_prune.SmartHttpPushClient", BombClient)
    assert prune_specs(repo, "origin", plan, ("main",)) == ()


def test_run_push_prune_routes_planned_deletion(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_prune(repo_obj, remote, plan, explicit_refspecs=(), **kwargs):
        return (PushSpec("", "stale", namespace="heads", delete=True),)

    def fake_push_branch(repo_obj, remote, source, target, *, force=False):
        return {
            "status": "up-to-date",
            "remote": remote,
            "source_branch": source,
            "branch": target,
            "sha": repo_obj.refs.get_branch(source),
            "objects": 0,
        }

    def fake_delete(repo_obj, remote, target_ref, *, force=False):
        calls.append(target_ref)
        return {"status": "deleted", "remote": remote, "ref": target_ref, "objects": 0}

    monkeypatch.setattr("pygit.push_cli.prune_specs", fake_prune)
    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.setattr("pygit.push_cli.delete_remote_ref", fake_delete)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--prune", "origin", "refs/heads/*:refs/heads/*"]) == 0
    assert calls == ["refs/heads/stale"]


def test_run_push_rejects_delete_with_prune(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit):
        run_push(["--delete", "--prune", "origin", "old"])
