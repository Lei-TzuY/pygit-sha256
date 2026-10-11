"""Native Git oracle for annotated-tag translation and remote identity caches."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from pygit import Repository
from pygit.objects import TagObject
from pygit.remote import (
    NativeExporter,
    NativeImporter,
    PackParser,
    SmartHttpClient,
    build_pack,
    pkt_line,
)


@pytest.fixture
def native_remote(tmp_path, monkeypatch):
    source = tmp_path / "native.git"
    source.mkdir()
    env = dict(
        os.environ,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="Tag Author",
        GIT_AUTHOR_EMAIL="author@example.test",
        GIT_COMMITTER_NAME="Tag Author",
        GIT_COMMITTER_EMAIL="author@example.test",
        GIT_AUTHOR_DATE="1735689600 +0530",
        GIT_COMMITTER_DATE="1735689600 +0530",
    )

    def git(*args, input=None, cwd=source):
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout

    git("init", "--bare", "--quiet", "--object-format=sha1", "--initial-branch=main")
    blob = git("hash-object", "-w", "--stdin", input=b"hello\n").decode().strip()
    tree = git("mktree", input=f"100644 blob {blob}\thello.txt\n".encode()).decode().strip()
    commit = git("commit-tree", tree, input=b"first\n").decode().strip()
    git("update-ref", "refs/heads/main", commit)
    git("tag", "light")
    git("tag", "-a", "release", "-m", "Release annotation\n\nnotes café")
    git("tag", "-a", "outer", "release", "-m", "Nested annotation")
    git("tag", "-a", "payload", blob, "-m", "Blob annotation")
    git("tag", "-a", "snapshot", tree, "-m", "Tree annotation")
    advertised = git("upload-pack", "--stateless-rpc", "--advertise-refs", ".")
    advertisement = SmartHttpClient._parse_advertisement(
        pkt_line(b"# service=git-upload-pack\n") + b"0000" + advertised
    )
    wants = sorted(set(advertisement.refs.values()))
    objects = PackParser(
        git("pack-objects", "--stdout", "--revs", input=("\n".join(wants) + "\n").encode())
    ).parse()
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return self.body

    def urlopen(request, timeout):
        assert request.full_url.startswith("https://example.test/native.git/")
        requests.append(request)
        if "/info/refs?service=" in request.full_url:
            service = request.full_url.rsplit("=", 1)[1]
            assert service in ("git-upload-pack", "git-receive-pack")
            body = (
                pkt_line(f"# service={service}\n".encode())
                + b"0000"
                + git(service[4:], "--stateless-rpc", "--advertise-refs", ".")
            )
        elif request.full_url.endswith("/git-upload-pack"):
            body = git("upload-pack", "--stateless-rpc", ".", input=request.data)
        elif request.full_url.endswith("/git-receive-pack"):
            body = git("receive-pack", "--stateless-rpc", ".", input=request.data)
        else:
            raise AssertionError(request.full_url)
        return Response(body)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return SimpleNamespace(
        git=git, advertisement=advertisement, objects=objects, requests=requests
    )


def _clone(tmp_path):
    return Repository.clone("https://example.test/native.git", str(tmp_path / "clone"))


def _assert_tag_identities(repo, remote):
    mapping = repo._read_native_map()
    for name, native_oid in remote.advertisement.refs.items():
        if not name.startswith("refs/tags/") or name.endswith("^{}"):
            continue
        sha = repo.refs.get_tag(name[len("refs/tags/"):])
        assert mapping[sha] == native_oid
        if name.endswith("/light"):
            assert sha == repo.refs.resolve_head()
            continue
        obj = repo.store.read(sha)
        assert isinstance(obj, TagObject)
        expected = TagObject()
        expected.deserialize(remote.objects[native_oid].data)
        assert obj.tag_name == expected.tag_name
        assert obj.tagger == expected.tagger
        assert obj.message == expected.message
        assert obj.target_type == expected.target_type
        assert repo.store.read(obj.target_sha).type_name == obj.target_type
        assert mapping[obj.target_sha] == expected.target_sha
        assert sha != obj.target_sha


def test_clone_and_native_export_preserve_annotated_tag_graph(tmp_path, native_remote):
    repo = _clone(tmp_path)
    assert (repo.worktree / "hello.txt").read_bytes() == b"hello\n"
    _assert_tag_identities(repo, native_remote)
    exporter = NativeExporter(repo.store)
    for name, oid in native_remote.advertisement.refs.items():
        if name.startswith("refs/tags/") and not name.endswith("^{}"):
            sha = repo.refs.get_tag(name[len("refs/tags/"):])
            assert exporter.export_oid(sha) == oid
    assert {
        oid: (obj.type_name, obj.data) for oid, obj in exporter.objects.items()
    } == {
        oid: (obj.type_name, obj.data) for oid, obj in native_remote.objects.items()
    }
    oracle = tmp_path / "exported.git"
    oracle.mkdir()
    native_remote.git("init", "--bare", "--quiet", "--object-format=sha1", cwd=oracle)
    native_remote.git(
        "index-pack", "--stdin", input=build_pack(exporter.objects.values()), cwd=oracle
    )
    for name, oid in native_remote.advertisement.refs.items():
        if name.startswith("refs/") and not name.endswith("^{}"):
            native_remote.git("update-ref", name, oid, cwd=oracle)
    native_remote.git("fsck", "--strict", cwd=oracle)


def test_tagged_clone_refetch_and_incremental_push_use_commit_identity(tmp_path, native_remote):
    repo = _clone(tmp_path)
    first = repo.refs.resolve_head()
    native_first = native_remote.advertisement.refs["HEAD"]
    assert repo._read_native_map()[first] == native_first
    native_remote.requests.clear()
    assert repo.fetch()["objects"] == 0
    assert len(native_remote.requests) == 1
    assert native_remote.requests[0].data is None
    (repo.worktree / "hello.txt").write_bytes(b"second\n")
    repo.add(["hello.txt"])
    second = repo.commit("second")
    result = repo.push()
    assert result["status"] == "pushed"
    assert result["objects"] == 3
    native_second = native_remote.git("rev-parse", "main").decode().strip()
    assert repo._read_native_map()[second] == native_second
    assert f"parent {native_first}\n".encode() in native_remote.git("cat-file", "-p", native_second)
    native_remote.git("merge-base", "--is-ancestor", native_first, native_second)
    native_remote.git("fsck", "--strict")


def test_fetch_repairs_legacy_flattened_tag_cache(tmp_path, native_remote):
    repo = Repository.init(str(tmp_path / "legacy"))
    repo.add_remote("origin", "https://example.test/native.git")
    importer = NativeImporter(repo.store, native_remote.objects)
    ordinary = {
        oid: importer.import_oid(oid)
        for oid, obj in native_remote.objects.items()
        if obj.type_name != "tag"
    }
    refs = native_remote.advertisement.refs
    repo.refs.set_branch("main", ordinary[refs["HEAD"]])
    for name, oid in refs.items():
        if name.startswith("refs/tags/") and not name.endswith("^{}"):
            peeled = refs.get(name + "^{}", oid)
            repo.refs.set_tag(name[len("refs/tags/"):], ordinary[peeled])
    # Exact former cache shape: each annotated tag overwrote its target's entry.
    mapping = {sha: oid for oid, sha in ordinary.items()}
    mapping[ordinary[refs["HEAD"]]] = refs["refs/tags/outer"]
    for name in ("payload", "snapshot"):
        mapping[ordinary[refs[f"refs/tags/{name}^{{}}"]]] = refs[f"refs/tags/{name}"]
    repo._write_native_map(mapping)
    assert repo.fetch()["objects"] > 0
    _assert_tag_identities(repo, native_remote)
    for sha, oid in repo._read_native_map().items():
        assert repo.store.read(sha).type_name.decode() == native_remote.objects[oid].type_name
    assert repo.fetch()["objects"] == 0
