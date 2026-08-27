from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_cli import run_push
from pygit.push_defaults import PushSpec
from pygit.push_includes import (
    configured_force_if_includes,
    extract_force_if_includes,
    require_force_if_includes,
    resolve_force_if_includes,
)
from pygit.push_lease import LeasePolicy, LeaseRequest
from pygit.push_transport import delete_remote_ref, push_atomic_specs, push_branch
from pygit.remote import Advertisement, NativeExporter
from pygit.repo import Repository


def _commit(repo: Repository, name: str, text: str) -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "base.txt", "base")
    repo.add_remote("origin", "https://example.invalid/origin.git")
    return repo


def _record_remote(repo: Repository, branch: str, sha: str) -> str:
    exporter = NativeExporter(repo.store)
    native = exporter.export_oid(sha)
    native_map = repo._read_native_map("origin")
    native_map.update(exporter.converted)
    repo._write_native_map(native_map, "origin")
    repo.refs.set_remote("origin", branch, sha)
    return native


def _background_advanced(repo: Repository):
    """Make origin/main newer without ever putting that tip in main's reflog."""
    base = repo.refs.resolve_head()
    assert base
    repo.branch("remote-work", start_point="main")
    repo.refs.set_head_symbolic("remote-work", message="checkout: remote-work")
    remote_tip = _commit(repo, "remote.txt", "remote")
    remote_native = _record_remote(repo, "main", remote_tip)

    repo.refs.set_head_symbolic("main", message="checkout: main")
    local_tip = _commit(repo, "local.txt", "local")
    assert remote_tip not in repo._ancestor_distances(local_tip)
    assert all(
        entry.new_sha != remote_tip
        for entry in repo.refs.read_reflog("refs/heads/main")
    )
    return base, remote_tip, remote_native, local_tip


def _integrated_then_rewritten(repo: Repository):
    """Put the remote tip in main's reflog, then rewrite main away from it."""
    base = repo.refs.resolve_head()
    assert base
    remote_tip = _commit(repo, "remote.txt", "remote")
    remote_native = _record_remote(repo, "main", remote_tip)

    repo.refs.set_branch("main", base, message="reset: rewrite base")
    local_tip = _commit(repo, "local.txt", "local")
    assert remote_tip not in repo._ancestor_distances(local_tip)
    assert any(
        remote_tip in repo._ancestor_distances(entry.new_sha)
        for entry in repo.refs.read_reflog("refs/heads/main")
        if entry.new_sha != "0" * 64
    )
    return base, remote_tip, remote_native, local_tip


def _push_client(monkeypatch, remote_native: str, *, atomic: bool = False):
    observed = {"push": 0}

    if atomic:
        class FakeClient:
            def __init__(self, url):
                pass

            def discover(self):
                return Advertisement(
                    {"refs/heads/main": remote_native},
                    {"report-status", "atomic"},
                    {},
                )

            def push_many(self, updates, objects, advertisement=None):
                observed["push"] += 1
                return SimpleNamespace(objects_sent=len(objects))

        monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    else:
        class FakeClient:
            def __init__(self, url):
                pass

            def discover(self):
                return Advertisement(
                    {"refs/heads/main": remote_native},
                    {"report-status"},
                    {},
                )

            def push(self, ref_name, new_oid, objects, advertisement=None):
                observed["push"] += 1
                return SimpleNamespace(
                    old_oid=remote_native,
                    new_oid=new_oid,
                    objects_sent=len(objects),
                )

        monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)

    return observed


def test_extract_force_if_includes_uses_last_option_and_preserves_positionals():
    cleaned, override = extract_force_if_includes(
        [
            "--force-if-includes",
            "--no-force-if-includes",
            "--force-if-includes",
            "origin",
            "main",
        ]
    )
    assert cleaned == ("origin", "main")
    assert override is True

    cleaned, override = extract_force_if_includes(
        ["--force-if-includes", "--no-force-if-includes", "origin"]
    )
    assert cleaned == ("origin",)
    assert override is False


def test_push_use_force_if_includes_config_and_cli_override(tmp_path):
    repo = _repo(tmp_path)
    assert configured_force_if_includes(repo) is False
    repo.config_set("push", "useForceIfIncludes", "yes")
    assert configured_force_if_includes(repo) is True
    assert resolve_force_if_includes(repo, None) is True
    assert resolve_force_if_includes(repo, False) is False

    repo.config_set("push", "useForceIfIncludes", "off")
    assert configured_force_if_includes(repo) is False
    assert resolve_force_if_includes(repo, True) is True


def test_invalid_push_use_force_if_includes_config_is_rejected(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("push", "useForceIfIncludes", "maybe")
    with pytest.raises(RuntimeError, match="invalid boolean"):
        configured_force_if_includes(repo)


def test_background_fetch_not_integrated_in_main_reflog_is_rejected(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, _ = _background_advanced(repo)
    observed = _push_client(monkeypatch, remote_native)
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)

    with pytest.raises(RuntimeError, match="remote ref updated since checkout"):
        push_branch(repo, "origin", "main", "main", lease=policy)
    assert observed["push"] == 0


def test_remote_tip_present_in_old_main_reflog_allows_rewrite(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, remote_tip, remote_native, local_tip = _integrated_then_rewritten(repo)
    observed = _push_client(monkeypatch, remote_native)
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)

    result = push_branch(repo, "origin", "main", "main", lease=policy)
    assert result["status"] == "pushed"
    assert result["sha"] == local_tip
    assert remote_tip != local_tip
    assert observed["push"] == 1


def test_destination_branch_reflog_is_used_instead_of_different_source(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base, _, remote_native, _ = _integrated_then_rewritten(repo)
    repo.branch("topic", start_point=base)
    repo.refs.set_head_symbolic("topic", message="checkout: topic")
    topic_tip = _commit(repo, "topic.txt", "topic")
    observed = _push_client(monkeypatch, remote_native)
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)

    # topic's own history never included origin/main, but main's reflog did.
    result = push_branch(repo, "origin", "topic", "main", lease=policy)
    assert result["sha"] == topic_tip
    assert observed["push"] == 1


def test_missing_destination_local_branch_cannot_borrow_source_reflog(tmp_path):
    repo = _repo(tmp_path)
    _, remote_tip, _, _ = _integrated_then_rewritten(repo)
    repo.branch("topic", start_point=remote_tip)
    repo.refs.delete_branch("main")
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)

    with pytest.raises(RuntimeError, match="remote ref updated since checkout"):
        require_force_if_includes(
            True,
            policy,
            repo,
            "origin",
            "refs/heads/main",
        )


def test_explicit_expectation_makes_force_if_includes_a_noop(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, _ = _background_advanced(repo)
    observed = _push_client(monkeypatch, remote_native)
    policy = LeasePolicy(
        (
            LeaseRequest(
                "refs/heads/main",
                expect=remote_native,
                explicit_expect=True,
            ),
        ),
        force_if_includes=True,
    )

    result = push_branch(repo, "origin", "main", "main", lease=policy)
    assert result["status"] == "pushed"
    assert observed["push"] == 1


def test_missing_tracking_tip_has_nothing_extra_to_include(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement({}, {"report-status"}, {})

        def push(self, ref_name, new_oid, objects, advertisement=None):
            return SimpleNamespace(
                old_oid="0" * 40,
                new_oid=new_oid,
                objects_sent=len(objects),
            )

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    result = push_branch(repo, "origin", "main", "main", lease=policy)
    assert result["status"] == "pushed"


def test_delete_with_background_advanced_tracking_is_rejected(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, _ = _background_advanced(repo)
    called = {"push": False}

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": remote_native},
                {"report-status"},
                {},
            )

        def push(self, *args, **kwargs):
            called["push"] = True
            raise AssertionError("includes failure must reject before deletion POST")

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)
    with pytest.raises(RuntimeError, match="remote ref updated since checkout"):
        delete_remote_ref(repo, "origin", "refs/heads/main", lease=policy)
    assert called["push"] is False


def test_atomic_includes_failure_aborts_before_batch_post(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, _ = _background_advanced(repo)
    observed = _push_client(monkeypatch, remote_native, atomic=True)
    policy = LeasePolicy((LeaseRequest(None),), force_if_includes=True)

    with pytest.raises(RuntimeError, match="remote ref updated since checkout"):
        push_atomic_specs(
            repo,
            "origin",
            (PushSpec("main", "main"),),
            lease=policy,
        )
    assert observed["push"] == 0


def test_cli_config_enables_guard_and_no_option_disables_it(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("push", "useForceIfIncludes", "true")
    calls = []

    def fake_push_branch(repo_obj, remote, source, target, *, force=False, lease=None):
        calls.append((lease.force_if_includes if lease else None, force))
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

    assert run_push(["--force-with-lease", "origin", "main"]) == 0
    assert run_push([
        "--force-with-lease",
        "--no-force-if-includes",
        "origin",
        "main",
    ]) == 0
    assert calls == [(True, False), (False, False)]


def test_force_bypasses_force_if_includes_together_with_lease(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_push(self, remote="origin", force=False):
        calls.append((remote, force))
        return {
            "status": "pushed",
            "remote": remote,
            "branch": "main",
            "sha": self.refs.resolve_head(),
            "objects": 1,
        }

    monkeypatch.setattr(Repository, "push", fake_push)
    monkeypatch.chdir(repo.worktree)
    assert run_push([
        "--force",
        "--force-with-lease",
        "--force-if-includes",
        "origin",
        "main",
    ]) == 0
    assert calls == [("origin", True)]
