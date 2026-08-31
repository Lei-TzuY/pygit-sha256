from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import pygit.protocol_v2_packfile_uri_incremental_fetch as phase334
from pygit.loose_object_map import publish_staged_loose_object_map
from pygit.objects.blob import BlobObject
from pygit.objects.commit import CommitObject
from pygit.objects.tree import TreeObject
from pygit.protocol_v2_packfile_uri_batch import DownloadedPackfileUriBatch
from pygit.protocol_v2_packfile_uri_incremental import PackfileUriIncrementalState
from pygit.protocol_v2_packfile_uri_incremental_fetch import (
    IncrementalNamedRemotePackfileUriFetchResult,
    execute_incremental_packfile_uri_fetch_transaction,
    fetch_named_remote_incrementally_with_packfile_uris,
)
from pygit.protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from pygit.protocol_v2_packfile_uri_stage import (
    StagedPackfileUriImport,
    stage_packfile_uri_import,
)
from pygit.protocol_v2_packfile_uris import V2PackfileUriFetchResult
from pygit.refs import ZERO_SHA
from pygit.remote import Advertisement, NativeExporter, PackParser
from pygit.repo import Repository


EMPTY_BATCH = DownloadedPackfileUriBatch((), {}, 0)


def _local_commit_graph(repo: Repository):
    blob = BlobObject(b"phase334 existing payload\n")
    blob_oid = repo.store.write(blob)
    tree = TreeObject()
    tree.add_entry("100644", "payload.txt", blob_oid)
    tree_oid = repo.store.write(tree)
    commit = CommitObject(tree=tree_oid, message="phase334 old\n")
    commit_oid = repo.store.write(commit)

    exporter = NativeExporter(repo.store)
    native_commit = exporter.export_oid(commit_oid)
    native_to_local = {
        native: local for local, native in exporter.converted.items()
    }
    return commit_oid, native_commit, native_to_local


def _publish_map(repo: Repository, native_to_local):
    publish_staged_loose_object_map(
        repo,
        StagedPackfileUriImport(
            dict(native_to_local),
            tuple(sorted(set(native_to_local.values()))),
        ),
    )


def test_known_aware_stage_imports_native_increment_that_omits_old_tree_and_blob(tmp_path: Path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("native git not installed")

    native_repo = tmp_path / "native"
    subprocess.run([git, "init", str(native_repo)], check=True, stdout=subprocess.PIPE)
    subprocess.run([git, "-C", str(native_repo), "config", "user.name", "Phase334"], check=True)
    subprocess.run(
        [git, "-C", str(native_repo), "config", "user.email", "phase334@example.test"],
        check=True,
    )
    (native_repo / "payload.txt").write_text("stable payload\n")
    subprocess.run([git, "-C", str(native_repo), "add", "payload.txt"], check=True)
    subprocess.run(
        [git, "-C", str(native_repo), "commit", "-m", "old"],
        check=True,
        stdout=subprocess.PIPE,
    )
    old = subprocess.check_output(
        [git, "-C", str(native_repo), "rev-parse", "HEAD"], text=True
    ).strip()
    old_tree = subprocess.check_output(
        [git, "-C", str(native_repo), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()

    subprocess.run(
        [git, "-C", str(native_repo), "commit", "--allow-empty", "-m", "new"],
        check=True,
        stdout=subprocess.PIPE,
    )
    new = subprocess.check_output(
        [git, "-C", str(native_repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert subprocess.check_output(
        [git, "-C", str(native_repo), "rev-parse", "HEAD^{tree}"], text=True
    ).strip() == old_tree

    old_pack = subprocess.run(
        [git, "-C", str(native_repo), "pack-objects", "--stdout", "--revs"],
        input=(old + "\n").encode(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    incremental_pack = subprocess.run(
        [git, "-C", str(native_repo), "pack-objects", "--stdout", "--revs"],
        input=(new + "\n^" + old + "\n").encode(),
        check=True,
        stdout=subprocess.PIPE,
    ).stdout

    old_objects = PackParser(old_pack).parse()
    incremental_objects = PackParser(incremental_pack).parse()
    assert new in incremental_objects
    assert old_tree not in incremental_objects
    assert len(incremental_objects) == 1

    repo = Repository.init(str(tmp_path / "pygit"))
    old_staged = stage_packfile_uri_import(repo.store, old_objects, EMPTY_BATCH)
    assert old_tree in old_staged.native_to_local

    with pytest.raises(KeyError, match="missing object"):
        stage_packfile_uri_import(repo.store, incremental_objects, EMPTY_BATCH)

    new_staged = stage_packfile_uri_import(
        repo.store,
        incremental_objects,
        EMPTY_BATCH,
        known_native_to_local=old_staged.native_to_local,
    )
    local_new = new_staged.native_to_local[new]
    imported = repo.store.read(local_new)
    assert isinstance(imported, CommitObject)
    assert imported.tree == old_staged.native_to_local[old_tree]
    assert imported.parents == [old_staged.native_to_local[old]]


def test_known_stage_rejects_missing_local_mapping_before_import(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    obj = BlobObject(b"new\n")
    exporter = NativeExporter(repo.store)
    local = repo.store.write(obj)
    native = exporter.export_oid(local)
    native_obj = exporter.objects[native]

    with pytest.raises(RuntimeError, match="known local object is missing"):
        stage_packfile_uri_import(
            repo.store,
            {native: native_obj},
            EMPTY_BATCH,
            known_native_to_local={"a" * 40: "b" * 64},
        )


def test_refetched_native_object_must_agree_with_known_local_mapping(tmp_path: Path):
    repo = Repository.init(str(tmp_path / "repo"))
    old = BlobObject(b"old\n")
    old_local = repo.store.write(old)

    new = BlobObject(b"new\n")
    exporter = NativeExporter(repo.store)
    new_local = repo.store.write(new)
    new_native = exporter.export_oid(new_local)
    assert repo.store.delete(new_local)

    with pytest.raises(ValueError, match="contradicts its known"):
        stage_packfile_uri_import(
            repo.store,
            {new_native: exporter.objects[new_native]},
            EMPTY_BATCH,
            known_native_to_local={new_native: old_local},
        )


def test_incremental_transaction_passes_exact_known_map_to_stage(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "1" * 40
    local = "2" * 64
    incremental = PackfileUriIncrementalState((native,), {native: local}, ())
    publication = PackfileUriRefPublication(native, ZERO_SHA)
    batch = DownloadedPackfileUriBatch((), {}, 0)
    staged = StagedPackfileUriImport({native: "3" * 64}, ("3" * 64,))
    object_map = object()
    certificate = object()
    captured = {}

    monkeypatch.setattr(phase334, "download_packfile_uris", lambda *a, **k: batch)

    def fake_stage(store, inline, external, **kwargs):
        captured["known"] = kwargs["known_native_to_local"]
        return staged

    monkeypatch.setattr(phase334, "stage_packfile_uri_import", fake_stage)
    monkeypatch.setattr(phase334, "publish_staged_loose_object_map", lambda *a, **k: object_map)
    monkeypatch.setattr(phase334, "certify_packfile_uri_roots", lambda *a, **k: certificate)
    monkeypatch.setattr(phase334, "_acquire_publication_guard_locks", lambda repo: [])
    monkeypatch.setattr(phase334, "_assert_publication_state_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(phase334, "_release_publication_guard_locks", lambda locks: None)
    monkeypatch.setattr(
        phase334,
        "publish_packfile_uri_refs",
        lambda *a, **k: {"refs/remotes/origin/main": "3" * 64},
    )

    result = execute_incremental_packfile_uri_fetch_transaction(
        repo,
        (),
        {native: object()},
        {native: b"commit"},
        {"refs/remotes/origin/main": publication},
        incremental,
    )

    assert captured["known"] is incremental.known_native_to_local
    assert result.batch is batch
    assert result.staged is staged
    assert result.object_map is object_map
    assert result.certificate is certificate


def test_named_remote_sends_planned_have_and_same_known_map_to_transaction(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/acme/repo.git")
    old_local, old_native, native_to_local = _local_commit_graph(repo)
    _publish_map(repo, native_to_local)
    repo.refs.set_remote("origin", "main", old_local)

    new_native = "f" * 40
    advertisement = Advertisement(
        refs={"HEAD": new_native, "refs/heads/main": new_native},
        capabilities={"version 2"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    transport = V2PackfileUriFetchResult(advertisement, {}, (), (), ())
    captured = {}
    transaction_sentinel = object()

    class FakeClient:
        def __init__(self, url, timeout=30, *, server_options=()):
            self.url = url
            self.timeout = timeout

        def discover_refs(self):
            return advertisement

        def fetch_with_packfile_uris(self, protocols, **kwargs):
            captured["protocols"] = tuple(protocols)
            captured["haves"] = tuple(kwargs["haves"])
            captured["advertisement"] = kwargs["advertisement"]
            return transport

    monkeypatch.setattr(phase334, "SmartHttpV2PackfileUriClient", FakeClient)

    def fake_transaction(repo_arg, descriptors, inline, roots, publications, incremental, **kwargs):
        captured["incremental"] = incremental
        return transaction_sentinel

    monkeypatch.setattr(
        phase334,
        "execute_incremental_packfile_uri_fetch_transaction",
        fake_transaction,
    )

    result = fetch_named_remote_incrementally_with_packfile_uris(repo)

    assert isinstance(result, IncrementalNamedRemotePackfileUriFetchResult)
    assert result.incremental.haves == (old_native,)
    assert result.incremental.known_native_to_local == dict(sorted(native_to_local.items()))
    assert captured["haves"] == (old_native,)
    assert captured["incremental"] is result.incremental
    assert captured["advertisement"] is advertisement
    assert result.transaction is transaction_sentinel


def test_named_remote_without_map_coverage_preserves_full_fetch_behavior(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/acme/repo.git")
    new_native = "e" * 40
    advertisement = Advertisement(
        refs={"HEAD": new_native, "refs/heads/main": new_native},
        capabilities={"version 2"},
        symrefs={"HEAD": "refs/heads/main"},
    )
    transport = V2PackfileUriFetchResult(advertisement, {}, (), (), ())
    captured = {}

    class FakeClient:
        def __init__(self, url, timeout=30, *, server_options=()):
            self.timeout = timeout

        def discover_refs(self):
            return advertisement

        def fetch_with_packfile_uris(self, protocols, **kwargs):
            captured["haves"] = tuple(kwargs["haves"])
            return transport

    monkeypatch.setattr(phase334, "SmartHttpV2PackfileUriClient", FakeClient)
    monkeypatch.setattr(
        phase334,
        "execute_incremental_packfile_uri_fetch_transaction",
        lambda *args, **kwargs: object(),
    )

    result = fetch_named_remote_incrementally_with_packfile_uris(repo)

    assert captured["haves"] == ()
    assert result.incremental.known_native_to_local == {}
    assert result.incremental.fallback_refs == ("refs/remotes/origin/main",)


def test_initial_v0_fallback_stops_before_incremental_planning(tmp_path: Path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/acme/repo.git")

    class V0Client:
        def __init__(self, url, timeout=30, *, server_options=()):
            pass

        def discover_refs(self):
            return None

    monkeypatch.setattr(phase334, "SmartHttpV2PackfileUriClient", V0Client)
    monkeypatch.setattr(
        phase334,
        "plan_packfile_uri_incremental_state",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not plan after v0 fallback")),
    )

    assert fetch_named_remote_incrementally_with_packfile_uris(repo) is None
