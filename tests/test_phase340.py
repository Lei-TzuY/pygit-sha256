from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase340
from pygit.loose_object_map import publish_staged_loose_object_map
from pygit.objects.commit import CommitObject
from pygit.objects.tree import TreeObject
from pygit.protocol_v2_packfile_uri_connectivity import PackfileUriRootCertificate
from pygit.protocol_v2_packfile_uri_stage import StagedPackfileUriImport
from pygit.protocol_v2_packfile_uris import V2PackfileUriFetchResult
from pygit.remote import Advertisement, NativeExporter
from pygit.repo import Repository


REMOTE_URL = "https://example.test/repository.git"


def _mapped_tracking_tip(repo: Repository):
    tree = TreeObject()
    tree_oid = repo.store.write(tree)
    commit = CommitObject(tree=tree_oid, message="phase340 known-only\n")
    local_tip = repo.store.write(commit)

    exporter = NativeExporter(repo.store)
    native_tip = exporter.export_oid(local_tip)
    native_to_local = {
        native: local for local, native in exporter.converted.items()
    }
    publish_staged_loose_object_map(
        repo,
        StagedPackfileUriImport(
            dict(sorted(native_to_local.items())),
            tuple(sorted(set(native_to_local.values()))),
        ),
    )
    repo.refs.set_remote("origin", "main", local_tip)
    advertisement = Advertisement(
        refs={
            "HEAD": native_tip,
            "refs/heads/main": native_tip,
        },
        capabilities=set(),
        symrefs={"HEAD": "refs/heads/main"},
    )
    return local_tip, native_tip, advertisement


class _KnownOnlyClient:
    def __init__(self, url: str, timeout: int = 30, server_options=()):
        self.url = url
        self.timeout = timeout
        self.server_options = tuple(server_options)
        self.advertisement = None
        self.haves = None

    def discover_refs(self):
        return self.advertisement

    def fetch_with_packfile_uris(
        self,
        protocols,
        *,
        haves=(),
        advertisement=None,
        shallow=(),
        deepen=None,
        deepen_relative=False,
    ):
        self.haves = tuple(haves)
        return V2PackfileUriFetchResult(
            advertisement=advertisement,
            objects={},
            shallow=(),
            unshallow=(),
            packfile_uris=(),
        )


def _install_known_only_client(monkeypatch, advertisement: Advertisement):
    instances = []

    class Client(_KnownOnlyClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.advertisement = advertisement
            instances.append(self)

    monkeypatch.setattr(phase340, "SmartHttpV2PackfileUriClient", Client)
    monkeypatch.setattr(phase340, "_configured_remote_url", lambda repo, remote: REMOTE_URL)
    return instances


def test_fetch_head_projection_uses_certified_local_sha256_and_source_branch_names():
    native = "a" * 40
    local = "b" * 64
    plan = phase340.PackfileUriRemoteTrackingPlan(
        expected_roots={native: b"commit"},
        publications={
            "refs/remotes/origin/topic/nested": phase340.PackfileUriRefPublication(
                native,
                "c" * 64,
            )
        },
        default_branch=None,
    )
    certificate = PackfileUriRootCertificate(
        native_to_local={native: local},
        expected_types={native: b"commit"},
    )

    assert phase340._fetch_head_refs("origin", plan, certificate) == {
        "refs/heads/topic/nested": local
    }


def test_named_incremental_explicit_branch_rewrites_fetch_head_as_mergeable(
    tmp_path: Path,
    monkeypatch,
):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, native_tip, advertisement = _mapped_tracking_tip(repo)
    instances = _install_known_only_client(monkeypatch, advertisement)
    fetch_head = repo.pygit_dir / "FETCH_HEAD"
    fetch_head.write_text("sentinel from older fetch\n", encoding="utf-8")

    result = phase340.fetch_named_remote_incrementally_with_packfile_uris(
        repo,
        branches=("refs/heads/main",),
    )

    assert result is not None
    assert result.transaction.object_map is None
    assert result.transaction.published_refs == {"refs/remotes/origin/main": local_tip}
    assert instances[0].haves == (native_tip,)
    assert fetch_head.read_text(encoding="utf-8") == (
        f"{local_tip}\t\tbranch 'main' of {REMOTE_URL}\n"
    )
    assert native_tip not in fetch_head.read_text(encoding="utf-8")
    assert len(fetch_head.read_text(encoding="utf-8").split("\t", 1)[0]) == 64


def test_named_incremental_default_selection_marks_fetch_head_not_for_merge(
    tmp_path: Path,
    monkeypatch,
):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, _, advertisement = _mapped_tracking_tip(repo)
    _install_known_only_client(monkeypatch, advertisement)

    result = phase340.fetch_named_remote_incrementally_with_packfile_uris(repo)

    assert result is not None
    assert (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8") == (
        f"{local_tip}\tnot-for-merge\tbranch 'main' of {REMOTE_URL}\n"
    )


def test_named_incremental_branch_selection_failure_clears_stale_fetch_head(
    tmp_path: Path,
    monkeypatch,
):
    repo = Repository.init(str(tmp_path / "repo"))
    _, _, advertisement = _mapped_tracking_tip(repo)
    _install_known_only_client(monkeypatch, advertisement)
    fetch_head = repo.pygit_dir / "FETCH_HEAD"
    fetch_head.write_text("sentinel from older fetch\n", encoding="utf-8")

    with pytest.raises(ValueError, match="was not advertised"):
        phase340.fetch_named_remote_incrementally_with_packfile_uris(
            repo,
            branches=("refs/heads/missing",),
        )

    assert fetch_head.exists()
    assert fetch_head.read_bytes() == b""


def test_named_incremental_ref_failure_keeps_certified_fetch_head(
    tmp_path: Path,
    monkeypatch,
):
    repo = Repository.init(str(tmp_path / "repo"))
    local_tip, _, advertisement = _mapped_tracking_tip(repo)
    _install_known_only_client(monkeypatch, advertisement)

    def fail_after_fetch_head(*args, **kwargs):
        current = (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8")
        assert current == f"{local_tip}\t\tbranch 'main' of {REMOTE_URL}\n"
        raise RuntimeError("simulated tracking-ref lock failure")

    monkeypatch.setattr(phase340, "publish_packfile_uri_refs", fail_after_fetch_head)

    with pytest.raises(RuntimeError, match="tracking-ref lock failure"):
        phase340.fetch_named_remote_incrementally_with_packfile_uris(
            repo,
            branches=("refs/heads/main",),
        )

    assert (repo.pygit_dir / "FETCH_HEAD").read_text(encoding="utf-8") == (
        f"{local_tip}\t\tbranch 'main' of {REMOTE_URL}\n"
    )
    assert repo.refs.get_remote("origin", "main") == local_tip


def test_v2_discovery_fallback_preserves_existing_fetch_head(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    fetch_head = repo.pygit_dir / "FETCH_HEAD"
    fetch_head.write_text("fallback must stay mutation-free\n", encoding="utf-8")

    class V0Client(_KnownOnlyClient):
        def discover_refs(self):
            return None

    monkeypatch.setattr(phase340, "SmartHttpV2PackfileUriClient", V0Client)
    monkeypatch.setattr(phase340, "_configured_remote_url", lambda repo, remote: REMOTE_URL)

    assert phase340.fetch_named_remote_incrementally_with_packfile_uris(repo) is None
    assert fetch_head.read_text(encoding="utf-8") == "fallback must stay mutation-free\n"


def test_pre_ref_publication_hook_is_validated(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    _, _, advertisement = _mapped_tracking_tip(repo)
    plan = phase340.plan_packfile_uri_remote_tracking_publication(repo, advertisement)
    incremental = phase340.plan_packfile_uri_incremental_state(repo, plan)

    with pytest.raises(TypeError, match="pre-ref publication hook must be callable"):
        phase340.execute_incremental_packfile_uri_fetch_transaction(
            repo,
            (),
            {},
            plan.expected_roots,
            plan.publications,
            incremental,
            before_ref_publication=object(),
        )


def test_native_sha256_fetch_head_marker_truncation_and_ref_failure_ordering(tmp_path: Path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    source = tmp_path / "source"
    target = tmp_path / "target"

    def run(*args: str, check: bool = True):
        return subprocess.run(
            [git, *args],
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    run("init", "--object-format=sha256", "-b", "main", str(source))
    run("-C", str(source), "config", "user.name", "Phase340")
    run("-C", str(source), "config", "user.email", "phase340@example.test")
    (source / "payload").write_text("one\n", encoding="utf-8")
    run("-C", str(source), "add", "payload")
    run("-C", str(source), "commit", "-m", "one")
    run("-C", str(source), "branch", "feature")

    run("init", "--object-format=sha256", "-b", "work", str(target))
    run("-C", str(target), "remote", "add", "origin", str(source))

    run("-C", str(target), "fetch", "origin")
    default_lines = (target / ".git" / "FETCH_HEAD").read_text(encoding="utf-8").splitlines()
    assert len(default_lines) == 2
    assert all("\tnot-for-merge\tbranch '" in line for line in default_lines)
    assert all(len(line.split("\t", 1)[0]) == 64 for line in default_lines)

    run("-C", str(target), "fetch", "origin", "main", "feature")
    explicit_lines = (target / ".git" / "FETCH_HEAD").read_text(encoding="utf-8").splitlines()
    assert len(explicit_lines) == 2
    assert all("\t\tbranch '" in line for line in explicit_lines)

    fetch_head = target / ".git" / "FETCH_HEAD"
    fetch_head.write_text("stale\n", encoding="utf-8")
    missing = run("-C", str(target), "fetch", "origin", "missing", check=False)
    assert missing.returncode != 0
    assert fetch_head.read_bytes() == b""

    old_tracking = run(
        "-C", str(target), "rev-parse", "refs/remotes/origin/main"
    ).stdout.strip()
    (source / "payload").write_text("two\n", encoding="utf-8")
    run("-C", str(source), "add", "payload")
    run("-C", str(source), "commit", "-m", "two")
    new_tip = run("-C", str(source), "rev-parse", "HEAD").stdout.strip()

    lock = target / ".git" / "refs" / "remotes" / "origin" / "main.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("held\n", encoding="utf-8")
    failed_update = run("-C", str(target), "fetch", "origin", check=False)
    assert failed_update.returncode != 0
    failed_lines = fetch_head.read_text(encoding="utf-8").splitlines()
    assert any(
        line.startswith(f"{new_tip}\tnot-for-merge\tbranch 'main' of ")
        for line in failed_lines
    )
    assert run(
        "-C", str(target), "rev-parse", "refs/remotes/origin/main"
    ).stdout.strip() == old_tracking
