from __future__ import annotations

from types import SimpleNamespace

import pytest

from pygit.push_cli import run_push
from pygit.push_defaults import PushSpec
from pygit.push_mirror import configured_remote_mirror, mirror_specs
from pygit.remote import Advertisement
from pygit.repo import Repository


def _commit(repo: Repository, name: str = "a.txt", text: str = "A") -> str:
    path = repo.worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    repo.add([name])
    return repo.commit(text, author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    repo.add_remote("origin", "https://example.invalid/origin.git")
    return repo


def _install_advertisement(monkeypatch, refs):
    class FakeClient:
        def __init__(self, url):
            self.url = url

        def discover(self):
            return Advertisement(dict(refs), {"report-status"}, {})

    monkeypatch.setattr("pygit.push_mirror.SmartHttpPushClient", FakeClient)


def _write_ref(repo: Repository, refname: str, oid: str) -> None:
    assert refname.startswith("refs/")
    path = repo.pygit_dir / refname
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{oid}\n", encoding="utf-8")


def test_remote_mirror_config_defaults_false_and_parses_git_booleans(tmp_path):
    repo = _repo(tmp_path)
    assert configured_remote_mirror(repo, "origin") is False

    for value in ("true", "yes", "on", "1"):
        repo.config_set("remote", "origin.mirror", value)
        assert configured_remote_mirror(repo, "origin") is True

    for value in ("false", "no", "off", "0"):
        repo.config_set("remote", "origin.mirror", value)
        assert configured_remote_mirror(repo, "origin") is False

    repo.config_set("remote", "origin.mirror", "maybe")
    with pytest.raises(RuntimeError, match="remote.origin.mirror"):
        configured_remote_mirror(repo, "origin")


def test_mirror_specs_cover_all_refs_and_delete_remote_only_refs(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    repo.tag("v1")
    repo.refs.set_remote("upstream", "topic", head)
    _write_ref(repo, "refs/notes/demo", head)

    _install_advertisement(
        monkeypatch,
        {
            "HEAD": "1" * 40,
            "refs/heads/main": "2" * 40,
            "refs/heads/stale": "3" * 40,
            "refs/notes/demo": "4" * 40,
            "refs/notes/stale": "5" * 40,
            "refs/tags/v1": "6" * 40,
        },
    )

    specs = mirror_specs(repo, "origin")
    by_target = {spec.target_ref: spec for spec in specs}

    assert set(by_target) == {
        "refs/remotes/upstream/topic",
        "refs/notes/demo",
        "refs/tags/v1",
        "refs/heads/main",
        "refs/heads/stale",
        "refs/notes/stale",
    }
    assert by_target["refs/heads/main"].force is True
    assert by_target["refs/tags/v1"].force is True
    assert by_target["refs/notes/demo"].force is True
    assert by_target["refs/heads/stale"].delete is True
    assert by_target["refs/notes/stale"].delete is True
    assert "HEAD" not in by_target


def test_mirror_orders_remote_tracking_refs_before_heads(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = repo.refs.resolve_head()
    assert head
    repo.refs.set_remote("origin", "main", head)
    _install_advertisement(monkeypatch, {})

    specs = mirror_specs(repo, "origin")
    targets = [spec.target_ref for spec in specs if not spec.delete]
    assert targets.index("refs/remotes/origin/main") < targets.index("refs/heads/main")


def test_cli_explicit_mirror_routes_generic_refs_and_forces_updates(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    observed = []
    planned = (
        PushSpec("demo", "demo", force=True, namespace="notes"),
        PushSpec("main", "main", force=True),
        PushSpec("", "old", force=True, namespace="notes", delete=True),
    )
    monkeypatch.setattr("pygit.push_cli.mirror_specs", lambda repo_obj, remote: planned)

    def fake_push_ref(repo_obj, remote, source_ref, target_ref, **kwargs):
        observed.append(("ref", source_ref, target_ref, kwargs["force"]))
        return {"status": "pushed", "sha": repo_obj.refs.resolve_head(), "objects": 1}

    def fake_push_branch(repo_obj, remote, source, target, **kwargs):
        observed.append(("branch", source, target, kwargs["force"]))
        return {"status": "pushed", "sha": repo_obj.refs.resolve_head(), "objects": 1}

    def fake_delete(repo_obj, remote, target_ref, **kwargs):
        observed.append(("delete", target_ref, kwargs["force"]))
        return {"status": "deleted", "objects": 0}

    monkeypatch.setattr("pygit.push_cli.push_ref", fake_push_ref)
    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.setattr("pygit.push_cli.delete_remote_ref", fake_delete)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["--mirror", "origin"]) == 0
    assert observed == [
        ("ref", "refs/notes/demo", "refs/notes/demo", True),
        ("branch", "main", "main", True),
        ("delete", "refs/notes/old", True),
    ]


def test_remote_mirror_config_enables_mirror_without_cli_flag(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.mirror", "true")
    planned = (PushSpec("main", "main", force=True),)
    seen = []
    monkeypatch.setattr("pygit.push_cli.mirror_specs", lambda repo_obj, remote: planned)

    def fake_push_branch(repo_obj, remote, source, target, **kwargs):
        seen.append((remote, source, target, kwargs["force"]))
        return {"status": "pushed", "sha": repo_obj.refs.resolve_head(), "objects": 1}

    monkeypatch.setattr("pygit.push_cli.push_branch", fake_push_branch)
    monkeypatch.chdir(repo.worktree)

    assert run_push(["origin"]) == 0
    assert seen == [("origin", "main", "main", True)]


@pytest.mark.parametrize(
    "argv",
    [
        ["--mirror", "origin", "main"],
        ["--mirror", "--all", "origin"],
        ["--mirror", "--tags", "origin"],
        ["--mirror", "--delete", "origin", "main"],
    ],
)
def test_cli_mirror_rejects_native_incompatible_selection_modes(tmp_path, monkeypatch, argv):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit) as exc:
        run_push(argv)
    assert exc.value.code == 2


def test_configured_mirror_rejects_explicit_refspec(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.mirror", "true")
    monkeypatch.chdir(repo.worktree)

    with pytest.raises(SystemExit) as exc:
        run_push(["origin", "main"])
    assert exc.value.code == 2


def test_mirror_composes_with_atomic_batch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    planned = (
        PushSpec("demo", "demo", force=True, namespace="notes"),
        PushSpec("main", "main", force=True),
    )
    observed = []
    monkeypatch.setattr("pygit.push_cli.mirror_specs", lambda repo_obj, remote: planned)

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

    assert run_push(["--atomic", "--mirror", "origin"]) == 0
    assert [spec.target_ref for spec in observed] == [
        "refs/notes/demo",
        "refs/heads/main",
    ]
    assert all(spec.force for spec in observed)


def test_mirror_help_is_exposed(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit) as exc:
        run_push(["--help"])
    assert exc.value.code == 0
    assert "--mirror" in capsys.readouterr().out
