from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_atomic import AtomicSmartHttpPushClient
from pygit.push_cli import run_push
from pygit.push_defaults import PushSpec, all_branch_specs
from pygit.push_transport import push_atomic_specs
from pygit.remote import Advertisement, NativeObject, pkt_line
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


def test_atomic_client_sends_one_command_list_and_one_pack(monkeypatch):
    old_main = "a" * 40
    old_tag = "b" * 40
    new_main = "c" * 40
    new_tag = "d" * 40
    blob = NativeObject("blob", b"hello\n", "e" * 40)
    advertisement = Advertisement(
        {
            "refs/heads/main": old_main,
            "refs/tags/v1": old_tag,
        },
        {"report-status", "atomic"},
        {},
    )
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return (
                pkt_line(b"unpack ok\n")
                + pkt_line(b"ok refs/heads/main\n")
                + pkt_line(b"ok refs/tags/v1\n")
                + b"0000"
            )

    def fake_urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = AtomicSmartHttpPushClient("https://example.test/repo.git").push_many(
        [
            ("refs/heads/main", new_main),
            ("refs/tags/v1", new_tag),
        ],
        {blob.oid: blob},
        advertisement=advertisement,
    )

    assert len(requests) == 1
    body = requests[0].data
    assert requests[0].full_url.endswith("/git-receive-pack")
    assert f"{old_main} {new_main} refs/heads/main".encode() in body
    assert f"{old_tag} {new_tag} refs/tags/v1".encode() in body
    assert body.count(b"\x00report-status atomic") == 1
    assert body.count(b"PACK") == 1
    assert [update.ref_name for update in result.updates] == [
        "refs/heads/main",
        "refs/tags/v1",
    ]
    assert result.objects_sent == 1


def test_atomic_client_rejects_remote_without_atomic_before_post(monkeypatch):
    advertisement = Advertisement({}, {"report-status"}, {})

    def forbidden_urlopen(*args, **kwargs):
        raise AssertionError("unsupported atomic push must fail before POST")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_urlopen)
    with pytest.raises(RuntimeError, match="does not support atomic"):
        AtomicSmartHttpPushClient("https://example.test/repo.git").push_many(
            [("refs/heads/main", "a" * 40)],
            {},
            advertisement=advertisement,
        )


def test_atomic_client_surfaces_any_ref_rejection(monkeypatch):
    advertisement = Advertisement({}, {"report-status", "atomic"}, {})

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return (
                pkt_line(b"unpack ok\n")
                + pkt_line(b"ok refs/heads/main\n")
                + pkt_line(b"ng refs/heads/topic rejected by hook\n")
                + b"0000"
            )

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    with pytest.raises(RuntimeError, match="ng refs/heads/topic rejected by hook"):
        AtomicSmartHttpPushClient("https://example.test/repo.git").push_many(
            [
                ("refs/heads/main", "a" * 40),
                ("refs/heads/topic", "b" * 40),
            ],
            {},
            advertisement=advertisement,
        )


def test_atomic_transport_batches_all_branches_once(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")
    observed = {"pushes": 0}

    class FakeClient:
        def __init__(self, url):
            assert url == "https://example.invalid/origin.git"

        def discover(self):
            return Advertisement({}, {"report-status", "atomic"}, {})

        def push_many(self, updates, objects, advertisement=None):
            observed["pushes"] += 1
            observed["updates"] = list(updates)
            observed["objects"] = len(objects)
            return SimpleNamespace(objects_sent=len(objects))

    monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    specs = all_branch_specs(repo)
    results = push_atomic_specs(repo, "origin", specs)

    assert observed["pushes"] == 1
    assert [ref for ref, _ in observed["updates"]] == [
        "refs/heads/main",
        "refs/heads/topic",
    ]
    assert observed["objects"] > 0
    assert [spec.source for spec, _ in results] == ["main", "topic"]
    assert repo.refs.get_remote("origin", "main") == repo.refs.get_branch("main")
    assert repo.refs.get_remote("origin", "topic") == repo.refs.get_branch("topic")


def test_atomic_transport_does_not_mutate_cache_when_server_rejects(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement({}, {"report-status", "atomic"}, {})

        def push_many(self, updates, objects, advertisement=None):
            raise RuntimeError("ng refs/heads/topic rejected")

    monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="topic rejected"):
        push_atomic_specs(repo, "origin", all_branch_specs(repo))

    assert repo.refs.list_remotes("origin") == []
    assert repo._read_native_map("origin") == {}


def test_atomic_transport_preflights_all_refs_before_sending(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")
    called = {"push_many": False}

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "a" * 40},
                {"report-status", "atomic"},
                {},
            )

        def push_many(self, updates, objects, advertisement=None):
            called["push_many"] = True
            raise AssertionError("preflight failure must prevent receive-pack POST")

    monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="remote tip is not an ancestor"):
        push_atomic_specs(repo, "origin", all_branch_specs(repo))
    assert called["push_many"] is False
    assert repo.refs.list_remotes("origin") == []


def test_atomic_transport_can_mix_delete_and_create(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    repo.refs.set_remote("origin", "old", head)
    observed = {}

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/old": "a" * 40},
                {"report-status", "atomic"},
                {},
            )

        def push_many(self, updates, objects, advertisement=None):
            observed["updates"] = list(updates)
            return SimpleNamespace(objects_sent=len(objects))

    monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    specs = (
        PushSpec("", "old", namespace="heads", delete=True),
        PushSpec("main", "main"),
    )
    results = push_atomic_specs(repo, "origin", specs)

    assert [ref for ref, _ in observed["updates"]] == [
        "refs/heads/old",
        "refs/heads/main",
    ]
    assert observed["updates"][0][1] == "0" * 40
    assert [result["status"] for _, result in results] == ["deleted", "pushed"]
    assert repo.refs.get_remote("origin", "old") is None
    assert repo.refs.get_remote("origin", "main") == head


def test_atomic_transport_rejects_unsupported_remote_even_for_up_to_date_spec(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement({}, {"report-status"}, {})

    monkeypatch.setattr("pygit.push_transport.AtomicSmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="does not support atomic"):
        push_atomic_specs(repo, "origin", (PushSpec("main", "main"),))


def test_run_push_atomic_routes_the_whole_plan_to_batch_transport(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")
    calls = []

    def fake_atomic(repo_obj, remote, specs, *, force=False):
        calls.append((remote, [spec.source for spec in specs], force))
        return [
            (
                spec,
                {
                    "status": "pushed",
                    "remote": remote,
                    "ref": spec.target_ref,
                    "sha": repo_obj.refs.resolve(spec.source_ref),
                    "objects": 1 if index == 0 else 0,
                },
            )
            for index, spec in enumerate(specs)
        ]

    monkeypatch.setattr("pygit.push_cli.push_atomic_specs", fake_atomic)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--atomic", "--all", "origin"]) == 0
    assert calls == [("origin", ["main", "topic"], False)]


def test_run_push_atomic_single_ref_does_not_use_legacy_repo_push(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_atomic(repo_obj, remote, specs, *, force=False):
        calls.append((remote, len(specs)))
        spec = specs[0]
        return [
            (
                spec,
                {
                    "status": "pushed",
                    "remote": remote,
                    "ref": spec.target_ref,
                    "sha": repo_obj.refs.resolve(spec.source_ref),
                    "objects": 1,
                },
            )
        ]

    def forbidden_push(*args, **kwargs):
        raise AssertionError("--atomic must not fall back to Repository.push")

    monkeypatch.setattr("pygit.push_cli.push_atomic_specs", fake_atomic)
    monkeypatch.setattr(Repository, "push", forbidden_push)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--atomic", "origin", "main"]) == 0
    assert calls == [("origin", 1)]


def test_run_push_no_atomic_preserves_sequential_phase167_path(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")
    branch_calls = []

    def forbidden_atomic(*args, **kwargs):
        raise AssertionError("--no-atomic must preserve sequential transport")

    def fake_push_branch(repo_obj, remote, source, target, *, force=False):
        branch_calls.append((source, target, force))
        return {
            "status": "pushed",
            "remote": remote,
            "source_branch": source,
            "branch": target,
            "sha": repo_obj.refs.get_branch(source),
            "objects": 1,
        }

    monkeypatch.setattr("pygit.push_cli.push_atomic_specs", forbidden_atomic)
    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.chdir(repo.worktree)
    assert run_push(["--no-atomic", "--all", "origin"]) == 0
    assert branch_calls == [
        ("main", "main", False),
        ("topic", "topic", False),
    ]
