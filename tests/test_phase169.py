from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_cli import run_push
from pygit.push_defaults import PushSpec
from pygit.push_lease import LeasePolicy, LeaseRequest, extract_force_with_lease
from pygit.push_transport import delete_remote_ref, push_atomic_specs, push_branch, push_ref
from pygit.remote import Advertisement, NativeExporter
from pygit.repo import Repository


def _commit(repo: Repository, name: str, text: str) -> str:
    path = repo.worktree / name
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo, "a.txt", "base")
    repo.add_remote("origin", "https://example.invalid/origin.git")
    return repo


def _diverged(repo: Repository):
    base = repo.refs.resolve_head()
    assert base
    remote_tip = _commit(repo, "remote.txt", "remote")
    exporter = NativeExporter(repo.store)
    remote_native = exporter.export_oid(remote_tip)
    repo._write_native_map(exporter.converted, "origin")
    repo.refs.set_remote("origin", "main", remote_tip)

    repo.refs.set_branch("main", base)
    local_tip = _commit(repo, "local.txt", "local")
    assert remote_tip not in repo._ancestor_distances(local_tip)
    return base, remote_tip, remote_native, local_tip


def test_extract_bare_lease_does_not_consume_repository():
    cleaned, policy = extract_force_with_lease(
        ["--force-with-lease", "origin", "main"]
    )
    assert cleaned == ("origin", "main")
    assert policy == LeasePolicy((LeaseRequest(None),))


def test_no_force_with_lease_clears_all_preceding_requests():
    cleaned, policy = extract_force_with_lease(
        [
            "--force-with-lease",
            "--force-with-lease=main:deadbeef",
            "--no-force-with-lease",
            "origin",
        ]
    )
    assert cleaned == ("origin",)
    assert policy.active is False


def test_later_specific_lease_refines_global_request():
    _, policy = extract_force_with_lease(
        ["--force-with-lease", "--force-with-lease=main:"]
    )
    main = policy.request_for("refs/heads/main")
    topic = policy.request_for("refs/heads/topic")
    assert main is not None and main.explicit_expect and main.expect == ""
    assert topic == LeaseRequest(None)


def test_implicit_lease_allows_non_fast_forward_when_tracking_tip_matches(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, local_tip = _diverged(repo)
    observed = {}

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
            observed["ref"] = ref_name
            return SimpleNamespace(
                old_oid=remote_native,
                new_oid=new_oid,
                objects_sent=len(objects),
            )

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    policy = LeasePolicy((LeaseRequest(None),))
    result = push_branch(repo, "origin", "main", "main", lease=policy)
    assert result["status"] == "pushed"
    assert result["sha"] == local_tip
    assert observed["ref"] == "refs/heads/main"


def test_implicit_lease_rejects_stale_remote_before_push(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _diverged(repo)
    called = {"push": False}

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "f" * 40},
                {"report-status"},
                {},
            )

        def push(self, *args, **kwargs):
            called["push"] = True
            raise AssertionError("stale lease must reject before receive-pack")

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="stale info"):
        push_branch(
            repo,
            "origin",
            "main",
            "main",
            lease=LeasePolicy((LeaseRequest(None),)),
        )
    assert called["push"] is False


def test_implicit_lease_without_tracking_expects_remote_absence(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    policy = LeasePolicy((LeaseRequest(None),))

    class ExistingClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "a" * 40},
                {"report-status"},
                {},
            )

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", ExistingClient)
    with pytest.raises(RuntimeError, match="stale info"):
        push_branch(repo, "origin", "main", "main", lease=policy)

    class AbsentClient:
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

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", AbsentClient)
    result = push_branch(repo, "origin", "main", "main", lease=policy)
    assert result["status"] == "pushed"


def test_explicit_native_expectation_can_authorize_rewrite(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, _ = _diverged(repo)

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
            return SimpleNamespace(
                old_oid=remote_native,
                new_oid=new_oid,
                objects_sent=len(objects),
            )

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    _, policy = extract_force_with_lease(
        [f"--force-with-lease=main:{remote_native}"]
    )
    result = push_branch(repo, "origin", "main", "main", lease=policy)
    assert result["status"] == "pushed"


def test_explicit_local_revision_is_converted_to_native_expectation(tmp_path):
    repo = _repo(tmp_path)
    _, remote_tip, remote_native, _ = _diverged(repo)
    repo.refs.set_tag("seen", remote_tip)
    _, policy = extract_force_with_lease(["--force-with-lease=main:seen"])
    expected = policy.expected_native(
        repo,
        "origin",
        "refs/heads/main",
        repo._read_native_map("origin"),
    )
    assert expected == remote_native


def test_ref_specific_lease_does_not_force_an_unprotected_ref(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _, _, remote_native, _ = _diverged(repo)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/topic": remote_native},
                {"report-status"},
                {},
            )

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    repo.branch("topic", start_point="main")
    policy = LeasePolicy((LeaseRequest("refs/heads/main"),))
    with pytest.raises(RuntimeError, match="not an ancestor"):
        push_branch(repo, "origin", "topic", "topic", lease=policy)


def test_force_and_plus_refspec_bypass_stale_lease_in_cli(tmp_path, monkeypatch):
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
    assert run_push(["--force", "--force-with-lease=main:deadbeef", "origin", "main"]) == 0
    assert run_push(["--force-with-lease=main:deadbeef", "origin", "+main:main"]) == 0
    assert calls == [("origin", True), ("origin", True)]


def test_active_lease_routes_single_branch_away_from_legacy_repo_push(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def forbidden_push(*args, **kwargs):
        raise AssertionError("active lease must use the lease-aware transport")

    def fake_push_branch(repo_obj, remote, source, target, *, force=False, lease=None):
        calls.append((remote, source, target, force, lease.active if lease else False))
        return {
            "status": "pushed",
            "remote": remote,
            "source_branch": source,
            "branch": target,
            "sha": repo_obj.refs.get_branch(source),
            "objects": 1,
        }

    monkeypatch.setattr(Repository, "push", forbidden_push)
    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--force-with-lease", "origin", "main"]) == 0
    assert calls == [("origin", "main", "main", False, True)]


def test_delete_is_protected_by_lease(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    exporter = NativeExporter(repo.store)
    expected_native = exporter.export_oid(head)
    repo._write_native_map(exporter.converted, "origin")
    repo.refs.set_remote("origin", "old", head)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/old": "f" * 40},
                {"report-status"},
                {},
            )

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    policy = LeasePolicy((LeaseRequest("refs/heads/old"),))
    with pytest.raises(RuntimeError, match="stale info"):
        delete_remote_ref(repo, "origin", "refs/heads/old", lease=policy)
    assert repo.refs.get_remote("origin", "old") == head
    assert expected_native != "f" * 40


def test_atomic_stale_lease_rejects_whole_batch_before_send(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    repo.branch("topic")
    exporter = NativeExporter(repo.store)
    known_native = exporter.export_oid(head)
    repo._write_native_map(exporter.converted, "origin")
    repo.refs.set_remote("origin", "main", head)
    repo.refs.set_remote("origin", "topic", head)
    called = {"push_many": False}

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {
                    "refs/heads/main": known_native,
                    "refs/heads/topic": "f" * 40,
                },
                {"report-status", "atomic"},
                {},
            )

        def push_many(self, *args, **kwargs):
            called["push_many"] = True
            raise AssertionError("stale atomic lease must fail before POST")

    monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="refs/heads/topic"):
        push_atomic_specs(
            repo,
            "origin",
            (PushSpec("main", "main"), PushSpec("topic", "topic")),
            lease=LeasePolicy((LeaseRequest(None),)),
        )
    assert called["push_many"] is False
    assert repo.refs.get_remote("origin", "main") == head
    assert repo.refs.get_remote("origin", "topic") == head
