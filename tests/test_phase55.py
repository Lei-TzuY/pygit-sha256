"""Phase 55 tests: advanced cat-file batch/object-ish plumbing."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from pygit import Repository
from pygit.cat_file import inspect_object, object_exists, resolve_object
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, tree: str, parents: list[str], message: str, timestamp: int) -> str:
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _history(repo: Repository) -> tuple[str, str, str, str]:
    old_blob = repo.store.write(BlobObject(b"old\n"))
    root_tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", old_blob)]))
    root = _commit(repo, root_tree, [], "root", 1)

    blob = repo.store.write(BlobObject(b"hello\n"))
    nested_blob = repo.store.write(BlobObject(b"nested\n"))
    nested_tree = repo.store.write(TreeObject([TreeEntry("100644", "note.txt", nested_blob)]))
    tip_tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("040000", "dir", nested_tree),
                TreeEntry("100644", "file.txt", blob),
            ]
        )
    )
    tip = _commit(repo, tip_tree, [root], "tip", 2)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    return root, tip, blob, nested_blob


def _run(repo: Repository, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class TestObjectResolution:
    def test_resolves_refs_prefixes_ancestry_and_tree_paths(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        root, tip, blob, nested_blob = _history(repo)

        assert resolve_object(repo, "HEAD") == tip
        assert resolve_object(repo, tip[:12]) == tip
        assert resolve_object(repo, "HEAD~1") == root
        assert resolve_object(repo, "HEAD:file.txt") == blob
        assert resolve_object(repo, "HEAD:dir/note.txt") == nested_blob

        tree_oid = resolve_object(repo, "HEAD:")
        assert isinstance(repo.store.read(tree_oid), TreeObject)

    def test_missing_and_non_directory_paths_are_rejected(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _history(repo)

        assert not object_exists(repo, "HEAD:nope.txt")
        assert not object_exists(repo, "HEAD:file.txt/child")
        assert not object_exists(repo, "definitely-missing")

    def test_inspection_reports_raw_object_metadata(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, _, blob, _ = _history(repo)

        record = inspect_object(repo, "HEAD:file.txt")
        assert record.oid == blob
        assert record.type_name == "blob"
        assert record.size == 6
        assert record.content == b"hello\n"


class TestBatchCli:
    def test_batch_check_reports_metadata_and_missing_per_input(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, tip, blob, _ = _history(repo)

        result = _run(
            repo,
            "cat-file",
            "--batch-check",
            input_bytes=b"HEAD\nHEAD:file.txt\nmissing-object\n",
        )

        assert result.returncode == 0, result.stderr.decode()
        assert result.stdout.decode().splitlines() == [
            f"{tip} commit {len(repo.store.read(tip).serialize())}",
            f"{blob} blob 6",
            "missing-object missing",
        ]

    def test_batch_emits_header_then_raw_content(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, _, blob, _ = _history(repo)

        result = _run(repo, "cat-file", "--batch", input_bytes=b"HEAD:file.txt\n")

        assert result.returncode == 0, result.stderr.decode()
        assert result.stdout == f"{blob} blob 6\n".encode() + b"hello\n\n"

    def test_exists_mode_uses_objectish_and_tree_path_resolution(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _history(repo)

        found = _run(repo, "cat-file", "-e", "HEAD:dir/note.txt")
        missing = _run(repo, "cat-file", "-e", "HEAD:nope")

        assert found.returncode == 0
        assert found.stdout == b""
        assert missing.returncode == 1
        assert missing.stdout == b""

    def test_legacy_single_object_modes_still_delegate_unchanged(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _, _, blob, _ = _history(repo)

        result = _run(repo, "cat-file", "-t", blob)

        assert result.returncode == 0, result.stderr.decode()
        assert result.stdout == b"blob\n"
