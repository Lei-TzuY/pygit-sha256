"""Phase 143 tests: fsck --lost-found recovery materialization."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck_lost_found import write_lost_found
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


_ZERO_OID = "0" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, payload: bytes, *, message: str) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    identity = Identity("Tester", "tester@example.com", 1, "+0000")
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=identity,
            committer=identity,
            message=message,
        )
    )
    return blob, tree, commit


def _publish(repo: Repository, commit: str) -> None:
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")


def _write_log(repo: Repository, ref: str, line: str) -> None:
    relative = Path("HEAD") if ref == "HEAD" else Path(*ref.split("/"))
    path = repo.pygit_dir / "logs" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(line, encoding="utf-8")


def _record(old_oid: str, new_oid: str, message: str = "phase143") -> str:
    return (
        f"{old_oid} {new_oid} Tester <tester@example.com> 1 +0000"
        f"\t{message}\n"
    )


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_writer_uses_commit_and_other_directories_with_native_payloads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    standalone = repo.store.write(BlobObject(b"raw\x00blob\n"))
    _, tree, commit = _commit(repo, b"tree payload\n", message="lost commit")

    records = write_lost_found(repo, [commit, standalone, tree])

    assert [record.oid for record in records] == sorted([commit, standalone, tree])
    assert (repo.pygit_dir / "lost-found" / "commit" / commit).read_bytes() == (
        commit + "\n"
    ).encode("ascii")
    assert (repo.pygit_dir / "lost-found" / "other" / standalone).read_bytes() == b"raw\x00blob\n"
    assert (repo.pygit_dir / "lost-found" / "other" / tree).read_bytes() == (
        tree + "\n"
    ).encode("ascii")


def test_cli_lost_found_materializes_only_dangling_tips(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    reachable_blob, reachable_tree, reachable = _commit(repo, b"current\n", message="current")
    _publish(repo, reachable)
    lost_blob, lost_tree, lost_commit = _commit(repo, b"lost\n", message="lost")
    standalone = repo.store.write(BlobObject(b"standalone\x00bytes"))

    result = _run(repo, "fsck", "--lost-found")

    assert result.returncode == 0, result.stderr.decode()
    assert f"dangling commit {lost_commit}".encode() in result.stdout
    assert f"dangling blob {standalone}".encode() in result.stdout
    assert (repo.pygit_dir / "lost-found" / "commit" / lost_commit).read_text() == lost_commit + "\n"
    assert (repo.pygit_dir / "lost-found" / "other" / standalone).read_bytes() == b"standalone\x00bytes"

    # Objects below a dangling commit are unreachable, but not themselves
    # dangling tips, so --lost-found does not duplicate the whole closure.
    assert not (repo.pygit_dir / "lost-found" / "other" / lost_tree).exists()
    assert not (repo.pygit_dir / "lost-found" / "other" / lost_blob).exists()
    assert not (repo.pygit_dir / "lost-found" / "commit" / reachable).exists()
    assert not (repo.pygit_dir / "lost-found" / "other" / reachable_tree).exists()
    assert not (repo.pygit_dir / "lost-found" / "other" / reachable_blob).exists()


def test_cli_lost_found_honors_reflog_roots_and_no_reflogs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)
    _, _, recovered = _commit(repo, b"recovered\n", message="recovered")
    _write_log(repo, "HEAD", _record(current, recovered))

    protected = _run(repo, "fsck", "--lost-found")

    assert protected.returncode == 0, protected.stderr.decode()
    assert recovered.encode() not in protected.stdout
    assert not (repo.pygit_dir / "lost-found" / "commit" / recovered).exists()

    exposed = _run(repo, "fsck", "--lost-found", "--no-reflogs")

    assert exposed.returncode == 0, exposed.stderr.decode()
    assert f"dangling commit {recovered}".encode() in exposed.stdout
    assert (repo.pygit_dir / "lost-found" / "commit" / recovered).read_text() == recovered + "\n"


def test_lost_found_is_idempotent_and_replaces_stale_recovery_content(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"authoritative\n"))

    write_lost_found(repo, [blob])
    path = repo.pygit_dir / "lost-found" / "other" / blob
    path.write_bytes(b"stale")

    write_lost_found(repo, [blob])

    assert path.read_bytes() == b"authoritative\n"


def test_cli_does_not_write_recovery_files_when_fsck_has_errors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"would be dangling"))
    _write_log(repo, "HEAD", "not a reflog record\n")

    result = _run(repo, "fsck", "--lost-found")

    assert result.returncode == 1
    assert b"bad-reflog" in result.stderr
    assert not (repo.pygit_dir / "lost-found").exists()
    assert blob.encode() in result.stdout


def test_writer_refuses_symlinked_lost_found_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"safe"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.pygit_dir / "lost-found").symlink_to(outside, target_is_directory=True)

    try:
        write_lost_found(repo, [blob])
    except RuntimeError as exc:
        assert "safe directory" in str(exc)
    else:
        raise AssertionError("symlinked lost-found root should be rejected")

    assert list(outside.iterdir()) == []


def test_installed_help_advertises_lost_found(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "fsck", "--help")

    assert result.returncode == 0
    assert b"--lost-found" in result.stdout
