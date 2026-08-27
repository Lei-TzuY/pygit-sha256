"""Phase183 fetch pruning and tag-policy regressions."""

from __future__ import annotations

import hashlib

import pytest

from pygit import Repository
from pygit.fetch_cli import run_fetch
from pygit.fetch_configured import fetch_configured
from pygit.fetch_policy import configured_fetch_refspecs, parse_fetch_refspec, resolve_fetch_policy
from pygit.remote import Advertisement, FetchResult, NativeObject


def _repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    return repo


def _native_tag_oid(target: str, name: str = "v1") -> tuple[str, bytes]:
    payload = (
        f"object {target}\n"
        "type commit\n"
        f"tag {name}\n"
        "tagger Tester <test@example.com> 0 +0000\n"
        "\n"
        "annotation\n"
    ).encode()
    oid = hashlib.sha1(f"tag {len(payload)}\0".encode() + payload).hexdigest()
    return oid, payload


def test_fetch_refspec_maps_wildcard_both_directions():
    spec = parse_fetch_refspec("+refs/heads/*:refs/remotes/origin/*")
    assert spec.force is True
    assert spec.destination_for("refs/heads/topic") == "refs/remotes/origin/topic"
    assert spec.source_for_destination("refs/remotes/origin/topic") == "refs/heads/topic"


def test_empty_git_style_fetch_list_stays_empty_after_phase182(tmp_path):
    repo = _repo(tmp_path)
    (repo.pygit_dir / "config").write_text(
        "[remote]\norigin.url = https://example.test/repo.git\n",
        encoding="utf-8",
    )
    assert configured_fetch_refspecs(repo, "origin") == []


def test_fetch_policy_remote_prune_overrides_global_and_cli_overrides_remote(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("fetch", "prune", "true")
    repo.config_set("remote", "origin.prune", "false")
    assert resolve_fetch_policy(repo, "origin").prune is False
    assert resolve_fetch_policy(repo, "origin", prune=True).prune is True


def test_fetch_policy_tagopt_and_cli_precedence(tmp_path):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.tagOpt", "--no-tags")
    assert resolve_fetch_policy(repo, "origin").tag_mode == "none"
    assert resolve_fetch_policy(repo, "origin", tags=True).tag_mode == "all"
    assert resolve_fetch_policy(repo, "origin", tags=False).tag_mode == "none"


def test_prune_deletes_stale_remote_tracking_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo.refs.set_remote("origin", "gone", "b" * 64)
    repo._write_native_map({"a" * 64: "1" * 40}, "origin")

    class Client:
        def __init__(self, url):
            assert url == "https://example.test/repo.git"

        def discover(self):
            return Advertisement({"refs/heads/main": "1" * 40}, set(), {})

        def fetch(self, *args, **kwargs):
            raise AssertionError("known main should not need a pack")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    result = fetch_configured(repo, "origin", prune=True, tags=False)

    assert result["pruned"] == ["refs/remotes/origin/gone"]
    assert repo.refs.list_remotes("origin") == ["main"]


def test_prune_tags_requires_prune_tags_domain_not_plain_tags(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo.refs.set_tag("local-only", "b" * 64)
    repo._write_native_map({"a" * 64: "1" * 40}, "origin")

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement({"refs/heads/main": "1" * 40}, set(), {})

        def fetch(self, *args, **kwargs):
            raise AssertionError("all advertised refs are known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)

    fetch_configured(repo, "origin", prune=True, tags=True)
    assert repo.refs.get_tag("local-only") == "b" * 64

    result = fetch_configured(repo, "origin", prune=True, prune_tags=True, tags=False)
    assert "refs/tags/local-only" in result["pruned"]
    assert repo.refs.get_tag("local-only") is None


def test_prune_tags_without_prune_fetches_tags_but_does_not_delete_local_tags(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo.refs.set_remote("origin", "main", "a" * 64)
    repo.refs.set_tag("local-only", "b" * 64)
    repo._write_native_map(
        {"a" * 64: "1" * 40, "c" * 64: "3" * 40},
        "origin",
    )

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/tags/remote": "3" * 40},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("selected objects are already known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    result = fetch_configured(repo, "origin", prune=False, prune_tags=True, tags=False)

    assert result["pruned"] == []
    assert repo.refs.get_tag("local-only") == "b" * 64
    assert repo.refs.get_tag("remote") == "c" * 64


def test_no_tags_suppresses_automatic_tag_following(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo._write_native_map({"a" * 64: "1" * 40}, "origin")

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/tags/v1": "1" * 40},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("selected branch is already known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    result = fetch_configured(repo, "origin", tags=False)

    assert result["refs"] == {"refs/heads/main": "a" * 64}
    assert repo.refs.get_tag("v1") is None


def test_auto_follow_adds_missing_lightweight_tag_for_known_target(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo._write_native_map({"a" * 64: "1" * 40}, "origin")

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/tags/v1": "1" * 40},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("lightweight tag target is already known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    result = fetch_configured(repo, "origin")

    assert result["refs"]["refs/tags/v1"] == "a" * 64
    assert repo.refs.get_tag("v1") == "a" * 64


def test_auto_follow_fetches_annotated_tag_object_only_after_target_is_known(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    internal_target = "a" * 64
    native_target = "1" * 40
    tag_oid, tag_payload = _native_tag_oid(native_target)
    repo._write_native_map({internal_target: native_target}, "origin")
    calls = []

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {
                    "refs/heads/main": native_target,
                    "refs/tags/v1": tag_oid,
                    "refs/tags/v1^{}": native_target,
                },
                set(),
                {},
            )

        def fetch(self, haves=None, advertisement=None):
            calls.append(dict(advertisement.refs))
            assert advertisement.refs == {"refs/tags/v1": tag_oid}
            return FetchResult(
                advertisement,
                {tag_oid: NativeObject("tag", tag_payload, tag_oid)},
            )

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    result = fetch_configured(repo, "origin")

    assert calls == [{"refs/tags/v1": tag_oid}]
    assert result["objects"] == 1
    assert repo.refs.get_tag("v1") is not None
    assert repo.refs.get_tag("v1") != internal_target


def test_auto_follow_skips_tag_whose_target_is_unknown(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo._write_native_map({"a" * 64: "1" * 40}, "origin")
    tag_oid, _ = _native_tag_oid("9" * 40, "unrelated")

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {
                    "refs/heads/main": "1" * 40,
                    "refs/tags/unrelated": tag_oid,
                    "refs/tags/unrelated^{}": "9" * 40,
                },
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("unrelated tag object must not be wanted")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    fetch_configured(repo, "origin")
    assert repo.refs.get_tag("unrelated") is None


def test_tags_refuses_to_clobber_existing_tag_but_prune_tags_forces_update(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    repo.config_set("remote", "origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    repo.refs.set_tag("v1", "b" * 64)
    repo._write_native_map(
        {"a" * 64: "1" * 40, "c" * 64: "3" * 40},
        "origin",
    )

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/tags/v1": "3" * 40},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("all objects are known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)

    with pytest.raises(RuntimeError, match="clobber existing tag"):
        fetch_configured(repo, "origin", tags=True)
    assert repo.refs.get_tag("v1") == "b" * 64

    fetch_configured(repo, "origin", prune_tags=True, tags=False)
    assert repo.refs.get_tag("v1") == "c" * 64


def test_explicit_tag_fetch_mapping_survives_no_tags(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo.pygit_dir / "config").write_text(
        "[remote]\n"
        "origin.url = https://example.test/repo.git\n"
        "origin.fetch = +refs/heads/*:refs/remotes/origin/*\n"
        "origin.fetch = +refs/tags/*:refs/tags/*\n",
        encoding="utf-8",
    )
    repo._write_native_map(
        {"a" * 64: "1" * 40, "c" * 64: "3" * 40},
        "origin",
    )

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/main": "1" * 40, "refs/tags/v1": "3" * 40},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("all objects are known")

    monkeypatch.setattr("pygit.fetch_configured.SmartHttpClient", Client)
    fetch_configured(repo, "origin", tags=False)
    assert repo.refs.get_tag("v1") == "c" * 64


def test_fetch_cli_forwards_policy_flags(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    calls = []

    def fake_fetch(repo_arg, remote, **kwargs):
        calls.append((repo_arg.worktree, remote, kwargs))
        return {"refs": {}, "pruned": ["refs/remotes/origin/gone"]}

    monkeypatch.setattr("pygit.fetch_cli.fetch_configured", fake_fetch)
    monkeypatch.chdir(repo.worktree)
    assert run_fetch(["--prune", "--prune-tags", "--no-tags", "origin"]) == 0

    assert calls == [
        (
            repo.worktree,
            "origin",
            {"prune": True, "prune_tags": True, "tags": False},
        )
    ]
    assert "pruned 1 refs" in capsys.readouterr().out
