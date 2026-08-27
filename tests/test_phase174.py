from __future__ import annotations

import hashlib

from pygit.objects import Identity, TagObject
from pygit.push_transport import push_ref
from pygit.remote import Advertisement, NativeExporter, PackParser, PushResult, build_pack
from pygit.repo import Repository


def _commit(repo: Repository) -> str:
    path = repo.worktree / "a.txt"
    path.write_text("A", encoding="utf-8")
    repo.add(["a.txt"])
    return repo.commit("base", author_name="Test", author_email="test@example.com")


def _repo(tmp_path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    _commit(repo)
    return repo


def _annotated_tag(repo: Repository, name: str, target: str, target_type: bytes = b"commit") -> str:
    tag = TagObject(
        target_sha=target,
        target_type=target_type,
        tag_name=name,
        tagger=Identity("Tagger", "tagger@example.com", 1700000000, "+0000"),
        message=f"annotation for {name}",
    )
    oid = repo.store.write(tag)
    repo.refs.set_tag(name, oid)
    return oid


def _canonical_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def test_native_exporter_rewrites_annotated_tag_target_to_native_oid(tmp_path):
    repo = _repo(tmp_path)
    commit_sha = repo.refs.resolve_head()
    assert commit_sha
    tag_sha = _annotated_tag(repo, "v1", commit_sha)

    exporter = NativeExporter(repo.store)
    native_tag = exporter.export_oid(tag_sha)
    native_commit = exporter.converted[commit_sha]
    tag_object = exporter.objects[native_tag]

    assert tag_object.type_name == "tag"
    assert tag_object.data == (
        f"object {native_commit}\n"
        "type commit\n"
        "tag v1\n"
        "tagger Tagger <tagger@example.com> 1700000000 +0000\n"
        "\n"
        "annotation for v1"
    ).encode()
    assert native_tag == _canonical_oid("tag", tag_object.data)
    assert commit_sha not in tag_object.data.decode()


def test_native_exporter_recursively_rewrites_nested_annotated_tags(tmp_path):
    repo = _repo(tmp_path)
    commit_sha = repo.refs.resolve_head()
    assert commit_sha
    inner_sha = _annotated_tag(repo, "inner", commit_sha)
    outer_sha = _annotated_tag(repo, "outer", inner_sha, b"tag")

    exporter = NativeExporter(repo.store)
    native_outer = exporter.export_oid(outer_sha)
    native_inner = exporter.converted[inner_sha]

    assert exporter.objects[native_outer].type_name == "tag"
    assert exporter.objects[native_inner].type_name == "tag"
    assert exporter.objects[native_outer].data.startswith(
        f"object {native_inner}\ntype tag\ntag outer\n".encode()
    )


def test_tag_export_reuses_known_remote_target_without_resending_graph(tmp_path):
    repo = _repo(tmp_path)
    commit_sha = repo.refs.resolve_head()
    assert commit_sha
    tag_sha = _annotated_tag(repo, "remote-base", commit_sha)
    known_native = "1" * 40

    exporter = NativeExporter(
        repo.store,
        known_oids={commit_sha: known_native},
        have_shas={commit_sha},
    )
    native_tag = exporter.export_oid(tag_sha)

    assert exporter.converted[commit_sha] == known_native
    assert set(exporter.objects) == {native_tag}
    assert exporter.objects[native_tag].data.startswith(
        f"object {known_native}\ntype commit\n".encode()
    )


def test_build_pack_round_trips_exported_tag_as_obj_tag_type_4(tmp_path):
    repo = _repo(tmp_path)
    commit_sha = repo.refs.resolve_head()
    assert commit_sha
    tag_sha = _annotated_tag(repo, "packed", commit_sha)
    exporter = NativeExporter(repo.store)
    native_tag = exporter.export_oid(tag_sha)

    parsed = PackParser(build_pack(exporter.objects.values())).parse()

    assert native_tag in parsed
    assert parsed[native_tag].type_name == "tag"
    assert parsed[native_tag].data == exporter.objects[native_tag].data


def test_push_ref_can_transport_annotated_tag_object(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    commit_sha = repo.refs.resolve_head()
    assert commit_sha
    tag_sha = _annotated_tag(repo, "release", commit_sha)
    repo.add_remote("origin", "https://example.invalid/origin.git")
    observed = {}

    class FakeClient:
        def __init__(self, url):
            self.url = url

        def discover(self):
            return Advertisement({}, {"report-status"}, {})

        def push(self, ref_name, new_oid, objects, advertisement=None):
            observed["ref"] = ref_name
            observed["new_oid"] = new_oid
            observed["objects"] = dict(objects)
            return PushResult(advertisement, ref_name, "0" * 40, new_oid, len(objects))

    monkeypatch.setattr("pygit.push_transport.SmartHttpPushClient", FakeClient)
    monkeypatch.setattr("pygit.push_transport._run_pre_push", lambda *args, **kwargs: None)

    result = push_ref(repo, "origin", "refs/tags/release", "refs/tags/release")

    assert result["status"] == "pushed"
    assert result["sha"] == tag_sha
    assert observed["ref"] == "refs/tags/release"
    assert observed["new_oid"] in observed["objects"]
    assert observed["objects"][observed["new_oid"]].type_name == "tag"
