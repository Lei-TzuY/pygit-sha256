from __future__ import annotations

import hashlib
from contextlib import contextmanager

import pytest

from pygit.fetch_importer import PromisorFilteredNativeImporter
from pygit.fetch_partial import (
    _build_filtered_fetch_request,
    extract_filter_option,
    run_fetch,
)
from pygit.objects import BlobObject, CommitObject, TreeObject
from pygit.objects.tree import TreeEntry, _NATIVE_TREE_MAGIC
from pygit.promisor import (
    PromisorMissingError,
    read_promisor_state,
    update_promisor_state,
)
from pygit.protocol_v2 import ProtocolV2Capabilities
from pygit.remote import NativeObject, pkt_line
from pygit.repo import Repository


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _native_tree(blob_oid: str) -> bytes:
    return b"100644 a.txt\x00" + bytes.fromhex(blob_oid)


def _native_commit(tree_oid: str) -> bytes:
    return (
        f"tree {tree_oid}\n"
        "author Dev <dev@example.com> 0 +0000\n"
        "committer Dev <dev@example.com> 0 +0000\n"
        "\n"
        "filtered"
    ).encode()


def _caps(*, filter_feature: bool = True, server_option: bool = True):
    features = ["wait-for-done"]
    if filter_feature:
        features.append("filter")
    values = {
        "ls-refs": "unborn",
        "fetch": " ".join(features),
        "object-format": "sha1",
    }
    if server_option:
        values["server-option"] = None
    return ProtocolV2Capabilities(values)


def test_ordinary_tree_encoding_remains_sha256_native():
    local = "ab" * 32
    tree = TreeObject([TreeEntry("100644", "a.txt", local)])
    payload = tree.serialize()
    assert not payload.startswith(_NATIVE_TREE_MAGIC)
    assert payload.endswith(bytes.fromhex(local))


def test_promisor_tree_keeps_stable_identity_across_late_resolution(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native_blob = "12" * 20
    tree = TreeObject(
        [TreeEntry("100644", "a.txt", native_oid=native_blob)],
        native_entries=True,
    )
    tree_sha = repo.store.write(tree)

    unresolved = repo.store.read(tree_sha)
    with pytest.raises(PromisorMissingError, match=native_blob):
        _ = unresolved.entries[0].sha

    blob_sha = repo.store.write(BlobObject(b"hello\n"))
    update_promisor_state(
        repo.pygit_dir,
        promised={native_blob: "blob"},
        resolved={native_blob: blob_sha},
    )

    resolved = repo.store.read(tree_sha)
    assert resolved.entries[0].sha == blob_sha
    # Runtime resolution does not rewrite the native-reference tree payload.
    assert repo.store.write(resolved) == tree_sha


def test_filtered_importer_accepts_missing_blob_and_records_promise(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    blob_data = b"large omitted payload\n"
    blob_oid = _native_oid("blob", blob_data)
    tree_data = _native_tree(blob_oid)
    tree_oid = _native_oid("tree", tree_data)
    commit_data = _native_commit(tree_oid)
    commit_oid = _native_oid("commit", commit_data)

    objects = {
        tree_oid: NativeObject("tree", tree_data, tree_oid),
        commit_oid: NativeObject("commit", commit_data, commit_oid),
    }
    importer = PromisorFilteredNativeImporter(
        repo.store,
        objects,
        remote="origin",
        filter_spec="blob:none",
    )
    commit_sha = importer.import_oid(commit_oid)
    commit = repo.store.read(commit_sha)
    assert isinstance(commit, CommitObject)
    tree = repo.store.read(commit.tree)
    assert isinstance(tree, TreeObject)
    assert tree.native_entries is True
    with pytest.raises(PromisorMissingError, match=blob_oid):
        _ = tree.entries[0].sha

    state = read_promisor_state(repo.pygit_dir)
    assert state["remotes"]["origin"]["filter"] == "blob:none"
    assert state["promised"][blob_oid] == "blob"


def test_filtered_importer_refuses_missing_subtree(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    missing_tree = "34" * 20
    root_data = b"40000 sub\x00" + bytes.fromhex(missing_tree)
    root_oid = _native_oid("tree", root_data)
    importer = PromisorFilteredNativeImporter(
        repo.store,
        {root_oid: NativeObject("tree", root_data, root_oid)},
        remote="origin",
        filter_spec="blob:none",
    )
    with pytest.raises(KeyError, match="missing required tree"):
        importer.import_oid(root_oid)


def test_filtered_request_frames_filter_after_capability_delimiter():
    want = "aa" * 20
    body = _build_filtered_fetch_request(
        _caps(),
        [want],
        haves=["bb" * 20],
        filter_spec="blob:none",
        server_options=["trace=one", "trace=two"],
    )
    first = body.index(pkt_line(b"server-option=trace=one\n"))
    second = body.index(pkt_line(b"server-option=trace=two\n"))
    delim = body.index(b"0001")
    filter_line = body.index(pkt_line(b"filter blob:none\n"))
    want_line = body.index(pkt_line(f"want {want}\n".encode()))
    assert first < second < delim < filter_line < want_line


def test_filtered_request_requires_server_filter_capability():
    with pytest.raises(RuntimeError, match="does not advertise filter"):
        _build_filtered_fetch_request(
            _caps(filter_feature=False),
            ["aa" * 20],
            filter_spec="blob:none",
        )


def test_filter_cli_parsing_and_terminator():
    forwarded, selected = extract_filter_option(
        ["--filter", "blob:limit=4096", "origin", "--", "--filter=literal"]
    )
    assert selected == "blob:limit=4096"
    assert forwarded == ["origin", "--", "--filter=literal"]


def test_filter_cli_rejects_unsupported_specs():
    with pytest.raises(RuntimeError, match="supports only"):
        extract_filter_option(["--filter=tree:0", "origin"])
    with pytest.raises(ValueError, match="positive byte count"):
        extract_filter_option(["--filter=blob:limit=0", "origin"])


def test_run_fetch_owns_explicit_server_options_and_strips_filter(tmp_path, monkeypatch):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    events = []

    monkeypatch.setattr("pygit.fetch_partial.find_repo", lambda: repo)

    @contextmanager
    def fake_transport(repo_arg, remote, filter_spec, *, server_options=()):
        events.append(("transport", remote, filter_spec, tuple(server_options)))
        yield

    monkeypatch.setattr("pygit.fetch_partial.partial_filter_transport", fake_transport)
    monkeypatch.setattr(
        "pygit.fetch_partial._run_fetch",
        lambda argv: events.append(("fetch", tuple(argv))) or 0,
    )

    assert run_fetch(
        [
            "--server-option=one",
            "--server-option=two",
            "--filter=blob:none",
            "origin",
        ]
    ) == 0
    assert events == [
        ("transport", "origin", "blob:none", ("one", "two")),
        ("fetch", ("origin",)),
    ]


def test_no_filter_delegates_without_new_transport(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "pygit.fetch_partial._run_fetch",
        lambda argv: calls.append(tuple(argv)) or 0,
    )
    assert run_fetch(["--update-shallow", "origin"]) == 0
    assert calls == [("--update-shallow", "origin")]
