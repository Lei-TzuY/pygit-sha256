from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.config import GitConfig
from pygit.push_cli import run_push
from pygit.push_options import (
    PushOptionAtomicSmartHttpPushClient,
    PushOptionSmartHttpPushClient,
    resolve_push_options,
    validate_push_options,
)
from pygit.push_transport import push_branch
from pygit.remote import Advertisement, NativeExporter, NativeObject, pkt_line
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


def test_push_option_validation_preserves_order_and_empty_value():
    assert validate_push_options(["ci.skip", "deploy=staging", ""]) == (
        "ci.skip",
        "deploy=staging",
        "",
    )
    with pytest.raises(RuntimeError, match="new line"):
        validate_push_options(["a\nb"])
    with pytest.raises(RuntimeError, match="NUL"):
        validate_push_options(["a\x00b"])


def test_git_config_get_all_preserves_duplicate_values_and_empty_reset(tmp_path):
    repo = _repo(tmp_path)
    repo.pygit_dir.joinpath("config").write_text(
        "[push]\n"
        "pushOption = inherited-a\n"
        "pushOption = inherited-b\n"
        "pushOption =\n"
        "pushOption = final-a\n"
        "pushOption = final-b\n",
        encoding="utf-8",
    )
    cfg = GitConfig(repo.pygit_dir)
    assert cfg.get_all("push", "pushOption") == ["final-a", "final-b"]


def test_cli_push_options_replace_configured_options(tmp_path):
    repo = _repo(tmp_path)
    repo.pygit_dir.joinpath("config").write_text(
        "[push]\n"
        "pushOption = from-config-a\n"
        "pushOption = from-config-b\n",
        encoding="utf-8",
    )
    assert resolve_push_options(repo, None) == ("from-config-a", "from-config-b")
    assert resolve_push_options(repo, ["from-cli", ""]) == ("from-cli", "")


def test_single_push_option_wire_framing(monkeypatch):
    old_oid = "a" * 40
    new_oid = "b" * 40
    blob = NativeObject("blob", b"hello\n", "c" * 40)
    advertisement = Advertisement(
        {"refs/heads/main": old_oid},
        {"report-status", "push-options"},
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
                + b"0000"
            )

    def fake_urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = PushOptionSmartHttpPushClient("https://example.test/repo.git").push_with_options(
        "refs/heads/main",
        new_oid,
        {blob.oid: blob},
        ("alpha", "beta=2"),
        advertisement=advertisement,
    )

    assert result.new_oid == new_oid
    assert len(requests) == 1
    body = requests[0].data
    first_flush = body.index(b"0000")
    alpha = body.index(pkt_line(b"alpha"))
    beta = body.index(pkt_line(b"beta=2"))
    pack = body.index(b"PACK")
    assert b"\x00report-status push-options" in body
    assert first_flush < alpha < beta < pack
    assert pkt_line(b"alpha\n") not in body


def test_explicit_empty_push_option_is_transmitted(monkeypatch):
    advertisement = Advertisement({}, {"report-status", "push-options"}, {})
    bodies = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b""

    def fake_urlopen(request, timeout):
        bodies.append(request.data)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    PushOptionSmartHttpPushClient("https://example.test/repo.git").push_with_options(
        "refs/heads/main",
        "b" * 40,
        {},
        ("",),
        advertisement=advertisement,
    )
    assert b"\x00report-status push-options" in bodies[0]
    assert b"00000004" in bodies[0]


def test_single_client_rejects_remote_without_push_options_before_post(monkeypatch):
    advertisement = Advertisement({}, {"report-status"}, {})

    def forbidden_urlopen(*args, **kwargs):
        raise AssertionError("unsupported push options must fail before POST")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_urlopen)
    with pytest.raises(RuntimeError, match="does not support push options"):
        PushOptionSmartHttpPushClient("https://example.test/repo.git").push_with_options(
            "refs/heads/main",
            "b" * 40,
            {},
            ("ci.skip",),
            advertisement=advertisement,
        )


def test_transport_rejects_unsupported_push_options_even_when_up_to_date(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    exporter = NativeExporter(repo.store)
    native = exporter.export_oid(head)
    repo._write_native_map(exporter.converted, "origin")
    repo.refs.set_remote("origin", "main", head)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": native},
                {"report-status"},
                {},
            )

        def push_with_options(self, *args, **kwargs):
            raise AssertionError("unsupported capability must fail before POST")

    monkeypatch.setattr("pygit.push_transport.PushOptionSmartHttpPushClient", FakeClient)
    with pytest.raises(RuntimeError, match="does not support push options"):
        push_branch(
            repo,
            "origin",
            "main",
            "main",
            push_options=("ci.skip",),
        )


def test_supported_up_to_date_push_option_does_not_post(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    exporter = NativeExporter(repo.store)
    native = exporter.export_oid(head)
    repo._write_native_map(exporter.converted, "origin")
    repo.refs.set_remote("origin", "main", head)

    class FakeClient:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": native},
                {"report-status", "push-options"},
                {},
            )

        def push_with_options(self, *args, **kwargs):
            raise AssertionError("up-to-date push must not invoke receive-pack hooks")

    monkeypatch.setattr("pygit.push_transport.PushOptionSmartHttpPushClient", FakeClient)
    result = push_branch(
        repo,
        "origin",
        "main",
        "main",
        push_options=("ci.skip",),
    )
    assert result["status"] == "up-to-date"


def test_atomic_push_option_wire_framing(monkeypatch):
    advertisement = Advertisement({}, {"report-status", "atomic", "push-options"}, {})
    blob = NativeObject("blob", b"payload", "d" * 40)
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
                + pkt_line(b"ok refs/heads/topic\n")
                + b"0000"
            )

    def fake_urlopen(request, timeout):
        requests.append(request)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = PushOptionAtomicSmartHttpPushClient(
        "https://example.test/repo.git"
    ).push_many_with_options(
        [
            ("refs/heads/main", "a" * 40),
            ("refs/heads/topic", "b" * 40),
        ],
        {blob.oid: blob},
        ("one", "two"),
        advertisement=advertisement,
    )

    assert len(result.updates) == 2
    body = requests[0].data
    first_flush = body.index(b"0000")
    one = body.index(pkt_line(b"one"))
    two = body.index(pkt_line(b"two"))
    pack = body.index(b"PACK")
    assert body.count(b"\x00report-status atomic push-options") == 1
    assert first_flush < one < two < pack


def test_cli_routes_options_away_from_legacy_repository_push(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def forbidden_push(*args, **kwargs):
        raise AssertionError("active push options must use the option-aware transport")

    def fake_push_branch(repo_obj, remote, source, target, *, force=False, push_options=()):
        calls.append((remote, source, target, tuple(push_options)))
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
    assert run_push(["-o", "alpha", "--push-option=beta", "origin", "main"]) == 0
    assert calls == [("origin", "main", "main", ("alpha", "beta"))]


def test_cli_uses_config_only_when_no_push_option_is_given(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.pygit_dir.joinpath("config").write_text(
        "[push]\n"
        "pushOption = config-a\n"
        "pushOption = config-b\n",
        encoding="utf-8",
    )
    calls = []

    def fake_push_branch(repo_obj, remote, source, target, *, force=False, push_options=()):
        calls.append(tuple(push_options))
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
    assert run_push(["origin", "main"]) == 0
    assert run_push(["-o", "cli-only", "origin", "main"]) == 0
    assert calls == [("config-a", "config-b"), ("cli-only",)]


def test_atomic_cli_passes_push_options_as_one_batch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.branch("topic")
    calls = []

    def fake_atomic(repo_obj, remote, specs, *, force=False, push_options=()):
        calls.append((remote, [spec.source for spec in specs], tuple(push_options)))
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
    assert run_push(["--atomic", "-o", "deploy=prod", "--all", "origin"]) == 0
    assert calls == [("origin", ["main", "topic"], ("deploy=prod",))]
