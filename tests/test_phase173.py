from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.objects import Identity, TagObject
from pygit.push_cli import run_push
from pygit.push_defaults import PushPlan, PushSpec, all_tag_specs
from pygit.push_follow_tags import (
    configured_follow_tags,
    follow_tag_specs,
    resolve_follow_tags,
)
from pygit.remote import Advertisement
from pygit.repo import Repository


def _commit(repo: Repository, name: str, text: str) -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "A")
    repo.add_remote("origin", "https://example.invalid/origin.git")
    return repo


def _advertisement(monkeypatch, refs=None):
    refs = dict(refs or {})

    class FakeClient:
        def __init__(self, url):
            self.url = url

        def discover(self):
            return Advertisement(refs, {"report-status"}, {})

    monkeypatch.setattr("pygit.push_follow_tags.SmartHttpPushClient", FakeClient)


def _main_plan() -> PushPlan:
    return PushPlan("origin", (PushSpec("main", "main"),), "explicit")


def test_follow_tags_config_defaults_false_and_accepts_git_booleans(tmp_path):
    repo = _repo(tmp_path)
    assert configured_follow_tags(repo) is False
    assert resolve_follow_tags(repo, None) is False

    for value in ("true", "yes", "on", "1"):
        repo.config_set("push", "followTags", value)
        assert configured_follow_tags(repo) is True

    for value in ("false", "no", "off", "0"):
        repo.config_set("push", "followTags", value)
        assert configured_follow_tags(repo) is False

    repo.config_set("push", "followTags", "maybe")
    with pytest.raises(RuntimeError, match="push.followTags"):
        configured_follow_tags(repo)


def test_cli_override_wins_over_follow_tags_config(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "followTags", "true")
    assert resolve_follow_tags(repo, False) is False
    repo.config_set("push", "followTags", "false")
    assert resolve_follow_tags(repo, True) is True


def test_follow_tags_selects_reachable_annotated_tags_only(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = repo.refs.resolve_head()
    assert base
    repo.tag("ann-base", annotated=True, message="base")
    repo.tag("light-base")

    repo.branch("other")
    tip = _commit(repo, "main.txt", "main")
    repo.tag("ann-tip", annotated=True, message="tip")

    repo.checkout("other")
    unrelated = _commit(repo, "other.txt", "other")
    repo.tag("ann-other", annotated=True, message="other")
    repo.checkout("main")

    assert base in repo._ancestor_distances(tip)
    assert unrelated not in repo._ancestor_distances(tip)
    _advertisement(monkeypatch)

    specs = follow_tag_specs(repo, "origin", _main_plan(), ("main",))
    assert [(spec.namespace, spec.source, spec.target) for spec in specs] == [
        ("tags", "ann-base", "ann-base"),
        ("tags", "ann-tip", "ann-tip"),
    ]


def test_follow_tags_skips_remote_tag_even_when_remote_value_differs(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("release", annotated=True, message="release")
    _advertisement(monkeypatch, {"refs/tags/release": "f" * 40})

    assert follow_tag_specs(repo, "origin", _main_plan(), ("main",)) == ()


def test_follow_tags_honors_negative_tag_refspecs(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("keep", annotated=True, message="keep")
    repo.tag("skip-one", annotated=True, message="skip")
    repo.tag("skip-two", annotated=True, message="skip")
    _advertisement(monkeypatch)

    specs = follow_tag_specs(
        repo,
        "origin",
        _main_plan(),
        ("main", "^refs/tags/skip-*"),
    )
    assert [spec.source for spec in specs] == ["keep"]


def test_follow_tags_honors_negative_tag_refspec_from_remote_push_config(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("v1", annotated=True, message="v1")
    repo.tag("v2", annotated=True, message="v2")
    repo.config_set(
        "remote",
        "origin.push",
        "main ^refs/tags/v1",
    )
    _advertisement(monkeypatch)

    plan = PushPlan("origin", (PushSpec("main", "main"),), "remote.push")
    specs = follow_tag_specs(repo, "origin", plan)
    assert [spec.source for spec in specs] == ["v2"]


def test_nested_annotated_tags_are_followed_when_they_peel_to_reachable_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("inner", annotated=True, message="inner")
    inner_oid = repo.refs.get_tag("inner")
    assert inner_oid
    outer = TagObject(
        target_sha=inner_oid,
        target_type=b"tag",
        tag_name="outer",
        tagger=Identity("Test", "test@example.com", 1700000000, "+0000"),
        message="outer",
    )
    repo.refs.set_tag("outer", repo.store.write(outer))
    _advertisement(monkeypatch)

    specs = follow_tag_specs(repo, "origin", _main_plan(), ("main",))
    assert [spec.source for spec in specs] == ["inner", "outer"]


def test_explicit_lightweight_tag_source_can_trigger_reachable_annotated_tag(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("light")
    repo.tag("annotated", annotated=True, message="annotated")
    _advertisement(monkeypatch)

    plan = PushPlan(
        "origin",
        (PushSpec("light", "light", namespace="tags"),),
        "explicit",
    )
    specs = follow_tag_specs(repo, "origin", plan, ("refs/tags/light",))
    assert [spec.source for spec in specs] == ["annotated"]


def test_follow_tags_does_not_use_deletions_as_reachability_roots(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("release", annotated=True, message="release")

    class ForbiddenClient:
        def __init__(self, url):
            raise AssertionError("deletion-only follow-tags selection must not discover remote")

    monkeypatch.setattr("pygit.push_follow_tags.SmartHttpPushClient", ForbiddenClient)
    plan = PushPlan(
        "origin",
        (PushSpec("", "old", namespace="heads", delete=True),),
        "delete",
    )
    assert follow_tag_specs(repo, "origin", plan) == ()


def test_shallow_boundary_limits_follow_tag_reachability(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = repo.refs.resolve_head()
    assert base
    repo.tag("before-boundary", annotated=True, message="old")
    tip = _commit(repo, "b.txt", "B")
    repo.tag("at-boundary", annotated=True, message="tip")
    (repo.pygit_dir / "shallow").write_text(f"{tip}\n", encoding="utf-8")
    _advertisement(monkeypatch)

    specs = follow_tag_specs(repo, "origin", _main_plan(), ("main",))
    assert [spec.source for spec in specs] == ["at-boundary"]


def test_all_tags_plan_does_not_add_duplicate_follow_specs(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.tag("ann", annotated=True, message="ann")
    repo.tag("light")
    _advertisement(monkeypatch)
    plan = PushPlan("origin", all_tag_specs(repo), "tags")

    assert follow_tag_specs(repo, "origin", plan) == ()


def test_cli_follow_tags_adds_tag_to_sequential_push_plan(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("push", "followTags", "true")
    calls = []

    monkeypatch.setattr(
        "pygit.push_cli.follow_tag_specs",
        lambda repo_obj, remote, plan, raw: (PushSpec("release", "release", namespace="tags"),),
    )

    def fake_push_branch(repo_obj, remote, source, target, **kwargs):
        calls.append(("branch", source, target))
        return {"status": "pushed", "sha": repo_obj.refs.get_branch(source), "objects": 1}

    def fake_push_ref(repo_obj, remote, source_ref, target_ref, **kwargs):
        calls.append(("ref", source_ref, target_ref))
        return {"status": "pushed", "sha": repo_obj.refs.get_tag("release") or repo_obj.refs.resolve_head(), "objects": 1}

    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.setattr("pygit.push_cli.push_ref", fake_push_ref)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["origin", "main"]) == 0
    assert calls == [
        ("branch", "main", "main"),
        ("ref", "refs/tags/release", "refs/tags/release"),
    ]


def test_cli_no_follow_tags_overrides_config_and_preserves_legacy_single(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("push", "followTags", "true")
    calls = []

    def forbidden_follow(*args, **kwargs):
        raise AssertionError("--no-follow-tags must suppress follow-tag planning")

    def fake_push(self, remote="origin", force=False):
        calls.append((remote, force))
        return {
            "status": "pushed",
            "remote": remote,
            "branch": "main",
            "sha": self.refs.resolve_head(),
            "objects": 1,
        }

    monkeypatch.setattr("pygit.push_cli.follow_tag_specs", forbidden_follow)
    monkeypatch.setattr(Repository, "push", fake_push)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["--no-follow-tags", "origin", "main"]) == 0
    assert calls == [("origin", False)]


def test_cli_follow_tags_composes_with_atomic_batch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    observed = []

    monkeypatch.setattr(
        "pygit.push_cli.follow_tag_specs",
        lambda repo_obj, remote, plan, raw: (PushSpec("release", "release", namespace="tags"),),
    )

    def fake_atomic(repo_obj, remote, specs, **kwargs):
        observed.extend(specs)
        return [
            (
                spec,
                {
                    "status": "pushed",
                    "sha": repo_obj.refs.resolve(spec.source_ref),
                    "objects": 1,
                },
            )
            for spec in specs
        ]

    monkeypatch.setattr("pygit.push_cli.push_atomic_specs", fake_atomic)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["--atomic", "--follow-tags", "origin", "main"]) == 0
    assert [(spec.namespace, spec.source) for spec in observed] == [
        ("heads", "main"),
        ("tags", "release"),
    ]
