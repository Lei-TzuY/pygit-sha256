"""Phase 60 tests: repository fsck integrity and reachability checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck import fsck
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.pack import PackWriter


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, tree: str, parents: list[str] | None = None, message: str = "commit") -> str:
    identity = Identity("Tester", "tester@example.com", 1, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents or [],
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _history(repo: Repository) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(b"hello\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    commit = _commit(repo, tree)
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    return blob, tree, commit


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_healthy_graph_is_reachable_and_reports_only_dangling_storage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, tree, commit = _history(repo)
    dangling = repo.store.write(BlobObject(b"unreferenced\n"))

    report = fsck(repo)

    assert report.ok
    assert {blob, tree, commit}.issubset(report.reachable)
    assert dangling in report.unreachable
    assert dangling in report.dangling
    assert not report.errors


def test_missing_tree_target_is_reported_with_connectivity_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    missing = "a" * 64
    tree = repo.store.write(TreeObject([TreeEntry("100644", "missing.txt", missing)]))
    commit = _commit(repo, tree)
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")

    report = fsck(repo)

    assert not report.ok
    assert any(issue.code == "missing-object" and issue.oid == missing for issue in report.errors)


def test_tree_mode_and_target_type_are_checked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    nested = repo.store.write(TreeObject([]))
    root = repo.store.write(TreeObject([TreeEntry("100644", "not-a-blob", nested)]))
    commit = _commit(repo, root)
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")

    report = fsck(repo)

    assert any(issue.code == "wrong-object-type" and issue.oid == nested for issue in report.errors)


def test_shallow_boundary_allows_intentionally_missing_parent(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"tip\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "tip.txt", blob)]))
    missing_parent = "b" * 64
    tip = _commit(repo, tree, [missing_parent])
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    (repo.pygit_dir / "shallow").write_text(tip + "\n", encoding="utf-8")

    report = fsck(repo)

    assert report.ok
    assert not any(issue.oid == missing_parent for issue in report.issues)
    assert tip in report.reachable


def test_corrupt_loose_object_is_detected_and_cli_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, _, _ = _history(repo)
    path = repo.store.root / blob[:2] / blob[2:]
    path.write_bytes(b"not-a-zlib-object")

    report = fsck(repo)
    result = _run(repo, "fsck")

    assert any(issue.code == "object-read" and issue.oid == blob for issue in report.errors)
    assert result.returncode == 1
    assert b"object-read" in result.stderr


def test_pack_and_index_checksums_and_packed_only_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob, tree, commit = _history(repo)
    objects = [(oid, repo.store.read(oid)) for oid in (blob, tree, commit)]
    pack_path, idx_path = PackWriter(objects).write_pack_and_idx(repo.store.root / "pack")
    for oid in (blob, tree, commit):
        assert repo.store.delete(oid)

    healthy = fsck(repo)
    assert healthy.ok
    assert {blob, tree, commit}.issubset(healthy.checked_objects)

    data = bytearray(pack_path.read_bytes())
    data[-1] ^= 0x01
    pack_path.write_bytes(bytes(data))
    broken_pack = fsck(repo)
    assert any(issue.code == "bad-pack-checksum" for issue in broken_pack.errors)

    # Restore pack checksum, then independently damage the index checksum.
    data[-1] ^= 0x01
    pack_path.write_bytes(bytes(data))
    idx = bytearray(idx_path.read_bytes())
    idx[-1] ^= 0x01
    idx_path.write_bytes(bytes(idx))
    broken_index = fsck(repo)
    assert any(issue.code == "bad-pack-index-checksum" for issue in broken_index.errors)


def test_cli_dangling_unreachable_and_suppression_modes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _history(repo)
    orphan_tree = repo.store.write(TreeObject([]))
    orphan_commit = _commit(repo, orphan_tree, message="orphan")

    default = _run(repo, "fsck")
    unreachable = _run(repo, "fsck", "--unreachable")
    quiet_dangling = _run(repo, "fsck", "--no-dangling")

    assert default.returncode == 0, default.stderr.decode()
    assert f"dangling commit {orphan_commit}".encode() in default.stdout
    assert f"unreachable commit {orphan_commit}".encode() in unreachable.stdout
    assert f"unreachable tree {orphan_tree}".encode() in unreachable.stdout
    assert quiet_dangling.returncode == 0
    assert quiet_dangling.stdout == b""


def test_index_is_a_connectivity_root_and_validates_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"staged\n"))
    from pygit.index import IndexEntry

    repo.index.entries = {"staged.txt": IndexEntry("staged.txt", blob, "100644")}
    repo.index.save()
    report = fsck(repo)
    assert blob in report.reachable
    assert blob not in report.dangling

    repo.index.entries["staged.txt"].mode = "999999"
    repo.index.save()
    broken = fsck(repo)
    assert any(issue.code == "bad-index-mode" for issue in broken.errors)


def test_connectivity_only_still_finds_missing_links_without_scanning_dangling_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _history(repo)
    dangling = repo.store.write(BlobObject(b"ignored in connectivity-only\n"))

    report = fsck(repo, connectivity_only=True)

    assert report.ok
    assert dangling not in report.checked_objects
    assert dangling not in report.unreachable
