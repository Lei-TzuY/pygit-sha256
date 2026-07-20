"""Unit tests for smart HTTP framing, pack expansion, and SHA conversion."""

import hashlib
import struct
import zlib

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, TreeObject
from pygit.remote import (
    Advertisement,
    NativeExporter,
    NativeImporter,
    NativeObject,
    PackParser,
    SmartHttpClient,
    SmartHttpPushClient,
    PushResult,
    build_pack,
    pkt_line,
)
from pygit.store import ObjectStore


def _native_oid(type_name: str, data: bytes) -> str:
    return hashlib.sha1(f"{type_name} {len(data)}\0".encode() + data).hexdigest()


def _object_header(type_number: int, size: int) -> bytes:
    byte = (type_number << 4) | (size & 0x0F)
    size >>= 4
    result = bytearray()
    while size:
        result.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7
    result.append(byte)
    return bytes(result)


def _varint(value: int) -> bytes:
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


class TestSmartHttp:
    def test_parse_advertisement(self):
        sha = "a" * 40
        data = (
            pkt_line(b"# service=git-upload-pack\n")
            + b"0000"
            + pkt_line(
                f"{sha} HEAD\0side-band-64k ofs-delta "
                "symref=HEAD:refs/heads/main object-format=sha1\n".encode()
            )
            + pkt_line(f"{sha} refs/heads/main\n".encode())
            + b"0000"
        )

        advertisement = SmartHttpClient._parse_advertisement(data)

        assert advertisement.refs["HEAD"] == sha
        assert advertisement.refs["refs/heads/main"] == sha
        assert advertisement.symrefs["HEAD"] == "refs/heads/main"
        assert "ofs-delta" in advertisement.capabilities

    def test_fetch_sends_have_lines(self, monkeypatch):
        want = "a" * 40
        have = "b" * 40
        advertisement = Advertisement(
            {"refs/heads/main": want},
            {"multi_ack_detailed"},
            {},
        )
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return pkt_line(b"NAK\n") + build_pack([])

        def fake_urlopen(request, timeout):
            requests.append(request)
            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = SmartHttpClient("https://example.test/repo.git").fetch(
            haves={have},
            advertisement=advertisement,
        )

        assert result.objects == {}
        body = requests[0].data
        assert pkt_line(f"want {want} multi_ack_detailed\n".encode()) in body
        assert pkt_line(f"have {have}\n".encode()) in body
        assert body.endswith(pkt_line(b"done\n"))


class TestPackParser:
    def test_expand_ofs_delta_blob(self):
        base = b"hello world\n"
        target = b"hello pygit\n"
        base_entry = _object_header(3, len(base)) + zlib.compress(base)
        base_offset = 12
        delta_offset = base_offset + len(base_entry)
        distance = delta_offset - base_offset
        assert distance < 128
        delta = _varint(len(base)) + _varint(len(target)) + bytes([len(target)]) + target
        delta_entry = _object_header(6, len(delta)) + bytes([distance]) + zlib.compress(delta)
        body = b"PACK" + struct.pack(">II", 2, 2) + base_entry + delta_entry
        pack = body + hashlib.sha1(body).digest()

        objects = PackParser(pack).parse()

        target_oid = _native_oid("blob", target)
        assert objects[target_oid].type_name == "blob"
        assert objects[target_oid].data == target


class TestNativeImporter:
    def test_convert_native_commit_graph_to_sha256_store(self, tmp_path):
        blob_data = b"hello\n"
        blob_oid = _native_oid("blob", blob_data)
        tree_data = b"100644 hello.txt\x00" + bytes.fromhex(blob_oid)
        tree_oid = _native_oid("tree", tree_data)
        commit_data = (
            f"tree {tree_oid}\n"
            "author Dev <dev@example.com> 0 +0000\n"
            "committer Dev <dev@example.com> 0 +0000\n"
            "\n"
            "initial"
        ).encode()
        commit_oid = _native_oid("commit", commit_data)
        native = {
            blob_oid: NativeObject("blob", blob_data, blob_oid),
            tree_oid: NativeObject("tree", tree_data, tree_oid),
            commit_oid: NativeObject("commit", commit_data, commit_oid),
        }
        store = ObjectStore(tmp_path / "objects")

        sha = NativeImporter(store, native).import_oid(commit_oid)

        commit = store.read(sha)
        assert isinstance(commit, CommitObject)
        tree = store.read(commit.tree)
        assert isinstance(tree, TreeObject)
        assert tree.entries[0].name == "hello.txt"
        assert len(sha) == 64

    def test_known_native_parent_can_be_reused(self, tmp_path):
        old_native = "a" * 40
        old_internal = "b" * 64
        blob_data = b"hello\n"
        blob_oid = _native_oid("blob", blob_data)
        tree_data = b"100644 hello.txt\x00" + bytes.fromhex(blob_oid)
        tree_oid = _native_oid("tree", tree_data)
        commit_data = (
            f"tree {tree_oid}\n"
            f"parent {old_native}\n"
            "author Dev <dev@example.com> 0 +0000\n"
            "committer Dev <dev@example.com> 0 +0000\n"
            "\n"
            "second"
        ).encode()
        commit_oid = _native_oid("commit", commit_data)
        native = {
            blob_oid: NativeObject("blob", blob_data, blob_oid),
            tree_oid: NativeObject("tree", tree_data, tree_oid),
            commit_oid: NativeObject("commit", commit_data, commit_oid),
        }

        sha = NativeImporter(
            ObjectStore(tmp_path / "objects"),
            native,
            known={old_native: old_internal},
        ).import_oid(commit_oid)

        commit = ObjectStore(tmp_path / "objects").read(sha)
        assert isinstance(commit, CommitObject)
        assert commit.parents == [old_internal]


class TestNativeExporter:
    def test_export_local_graph_as_parseable_native_pack(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        (tmp_path / "hello.txt").write_text("hello\n")
        repo.add(["hello.txt"])
        sha = repo.commit("initial", author_name="Dev", author_email="dev@example.com")

        exporter = NativeExporter(repo.store)
        native_sha = exporter.export_oid(sha)
        objects = PackParser(build_pack(exporter.objects.values())).parse()

        assert native_sha in objects
        assert objects[native_sha].type_name == "commit"
        assert b"initial" in objects[native_sha].data
        assert {obj.type_name for obj in objects.values()} == {"blob", "tree", "commit"}


class TestSmartHttpPush:
    def test_send_receive_pack_update(self, monkeypatch):
        old_oid = "a" * 40
        new_oid = "b" * 40
        blob = NativeObject("blob", b"hello\n", _native_oid("blob", b"hello\n"))
        advertisement = Advertisement(
            {"refs/heads/main": old_oid},
            {"report-status"},
            {},
        )
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return pkt_line(b"unpack ok\n") + pkt_line(b"ok refs/heads/main\n") + b"0000"

        def fake_urlopen(request, timeout):
            requests.append(request)
            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = SmartHttpPushClient("https://example.test/repo.git").push(
            "refs/heads/main",
            new_oid,
            {blob.oid: blob},
            advertisement=advertisement,
        )

        body = requests[0].data
        assert requests[0].full_url.endswith("/git-receive-pack")
        assert f"{old_oid} {new_oid} refs/heads/main".encode() in body
        assert PackParser(body[body.index(b"PACK"):]).parse()[blob.oid].data == b"hello\n"
        assert result.objects_sent == 1

    def test_repository_push_reuses_known_native_parent(self, tmp_path, monkeypatch):
        repo = Repository.init(str(tmp_path))
        repo.add_remote("origin", "https://example.test/repo.git")
        remote = {"oid": "0" * 40, "counts": []}

        class Client:
            def __init__(self, url):
                assert url == "https://example.test/repo.git"

            def discover(self):
                refs = {} if remote["oid"] == "0" * 40 else {"refs/heads/main": remote["oid"]}
                return Advertisement(refs, {"report-status"}, {})

            def push(self, ref_name, new_oid, objects, advertisement=None):
                remote["counts"].append(len(objects))
                old_oid = remote["oid"]
                remote["oid"] = new_oid
                return PushResult(advertisement, ref_name, old_oid, new_oid, len(objects))

        monkeypatch.setattr("pygit.remote.SmartHttpPushClient", Client)
        (tmp_path / "hello.txt").write_text("one\n")
        repo.add(["hello.txt"])
        first = repo.commit("first")

        assert repo.push()["status"] == "pushed"
        (tmp_path / "hello.txt").write_text("two\n")
        repo.add(["hello.txt"])
        second = repo.commit("second")
        assert repo.push()["status"] == "pushed"

        assert remote["counts"] == [3, 3]
        assert repo.refs.get_remote("origin", "main") == second
        native_map = repo._read_native_map()
        assert first in native_map
        assert second in native_map

    def test_repository_push_sends_full_graph_to_second_empty_remote(self, tmp_path, monkeypatch):
        repo = Repository.init(str(tmp_path))
        repo.add_remote("origin", "https://example.test/origin.git")
        repo.add_remote("backup", "https://example.test/backup.git")
        remotes = {
            "https://example.test/origin.git": {"oid": "0" * 40, "counts": []},
            "https://example.test/backup.git": {"oid": "0" * 40, "counts": []},
        }

        class Client:
            def __init__(self, url):
                self.remote = remotes[url]

            def discover(self):
                oid = self.remote["oid"]
                refs = {} if oid == "0" * 40 else {"refs/heads/main": oid}
                return Advertisement(refs, {"report-status"}, {})

            def push(self, ref_name, new_oid, objects, advertisement=None):
                self.remote["counts"].append(len(objects))
                old_oid = self.remote["oid"]
                self.remote["oid"] = new_oid
                return PushResult(advertisement, ref_name, old_oid, new_oid, len(objects))

        monkeypatch.setattr("pygit.remote.SmartHttpPushClient", Client)
        (tmp_path / "hello.txt").write_text("one\n")
        repo.add(["hello.txt"])
        repo.commit("first")
        repo.push("origin")
        (tmp_path / "hello.txt").write_text("two\n")
        repo.add(["hello.txt"])
        repo.commit("second")
        repo.push("origin")

        result = repo.push("backup")

        assert result["status"] == "pushed"
        assert remotes["https://example.test/origin.git"]["counts"] == [3, 3]
        assert remotes["https://example.test/backup.git"]["counts"] == [6]


class TestRemoteManagement:
    def test_remove_remote_deletes_config_tracking_refs_and_native_map(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        repo.add_remote("origin", "https://example.test/repo.git")
        repo.refs.set_remote("origin", "main", "a" * 64)
        repo._write_native_map({"b" * 64: "c" * 40}, "origin")

        repo.remove_remote("origin")

        assert repo.list_remotes() == {}
        assert repo.refs.list_remotes("origin") == []
        assert repo._read_native_map("origin") == {}

    def test_rename_remote_moves_config_tracking_refs_and_native_map(self, tmp_path):
        repo = Repository.init(str(tmp_path))
        repo.add_remote("origin", "https://example.test/repo.git")
        repo.refs.set_remote("origin", "main", "a" * 64)
        repo._write_native_map({"b" * 64: "c" * 40}, "origin")

        repo.rename_remote("origin", "upstream")

        assert repo.list_remotes() == {"upstream": "https://example.test/repo.git"}
        assert repo.refs.get_remote("upstream", "main") == "a" * 64
        assert repo.refs.list_remotes("origin") == []
        assert repo._read_native_map("upstream") == {"b" * 64: "c" * 40}

    def test_prune_remote_deletes_stale_tracking_branches(self, tmp_path, monkeypatch):
        repo = Repository.init(str(tmp_path))
        repo.add_remote("origin", "https://example.test/repo.git")
        repo.refs.set_remote("origin", "main", "a" * 64)
        repo.refs.set_remote("origin", "gone", "b" * 64)

        class Client:
            def __init__(self, url):
                assert url == "https://example.test/repo.git"

            def discover(self):
                return Advertisement({"refs/heads/main": "1" * 40}, set(), {})

        monkeypatch.setattr("pygit.remote.SmartHttpClient", Client)

        result = repo.prune_remote("origin")

        assert result == {"remote": "origin", "pruned": ["gone"]}
        assert repo.refs.list_remotes("origin") == ["main"]

    def test_fetch_skips_pack_when_advertised_refs_are_known(self, tmp_path, monkeypatch):
        repo = Repository.init(str(tmp_path))
        repo.add_remote("origin", "https://example.test/repo.git")
        internal = "a" * 64
        native = "b" * 40
        repo._write_native_map({internal: native}, "origin")

        class Client:
            def __init__(self, url):
                assert url == "https://example.test/repo.git"

            def discover(self):
                return Advertisement(
                    {"HEAD": native, "refs/heads/main": native},
                    {"symref=HEAD:refs/heads/main"},
                    {"HEAD": "refs/heads/main"},
                )

            def fetch(self, *args, **kwargs):
                raise AssertionError("known refs should not download a pack")

        monkeypatch.setattr("pygit.remote.SmartHttpClient", Client)

        result = repo.fetch("origin")

        assert result["objects"] == 0
        assert result["refs"] == {"HEAD": internal, "refs/heads/main": internal}
        assert repo.refs.get_remote("origin", "main") == internal
