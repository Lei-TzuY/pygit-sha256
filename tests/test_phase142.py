"""Phase 142 tests: fsck reflog recovery roots and --no-reflogs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck import fsck
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


_ZERO_OID = "0" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(
    repo: Repository,
    payload: bytes,
    *,
    parents: list[str] | None = None,
    message: str,
) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    identity = Identity("Tester", "tester@example.com", 1, "+0000")
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents or [],
            author=identity,
            committer=identity,
            message=message,
        )
    )
    return blob, tree, commit


def _publish(repo: Repository, commit: str) -> None:
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")


def _write_log(repo: Repository, ref: str, lines: list[str]) -> Path:
    relative = Path("HEAD") if ref == "HEAD" else Path(*ref.split("/"))
    path = repo.pygit_dir / "logs" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _record(old_oid: str, new_oid: str, message: str = "phase142") -> str:
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


def test_api_opt_in_treats_old_and_new_reflog_oids_as_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)

    old_blob, old_tree, old_commit = _commit(repo, b"old\n", message="old")
    new_blob, new_tree, new_commit = _commit(repo, b"new\n", message="new")
    _write_log(
        repo,
        "refs/heads/deleted",
        [_record(old_commit, new_commit, "deleted branch history")],
    )

    historical = fsck(repo)
    protected = fsck(repo, include_reflogs=True)

    assert old_commit in historical.unreachable
    assert new_commit in historical.unreachable
    assert {old_blob, old_tree, old_commit, new_blob, new_tree, new_commit}.issubset(
        protected.reachable
    )
    assert old_commit not in protected.unreachable
    assert new_commit not in protected.dangling
    assert protected.roots["reflog:refs/heads/deleted:1:old"] == old_commit
    assert protected.roots["reflog:refs/heads/deleted:1:new"] == new_commit


def test_zero_reflog_oid_is_not_added_as_a_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)
    _, _, recovered = _commit(repo, b"recovered\n", message="recovered")
    _write_log(repo, "HEAD", [_record(_ZERO_OID, recovered)])

    report = fsck(repo, include_reflogs=True)

    assert recovered in report.reachable
    assert _ZERO_OID not in report.roots.values()
    assert "reflog:HEAD:1:old" not in report.roots
    assert report.roots["reflog:HEAD:1:new"] == recovered


def test_connectivity_only_walks_reflog_only_object_graph(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)
    blob, tree, recovered = _commit(repo, b"recovered\n", message="recovered")
    _write_log(repo, "HEAD", [_record(current, recovered)])

    without = fsck(repo, connectivity_only=True)
    with_logs = fsck(repo, connectivity_only=True, include_reflogs=True)

    assert recovered not in without.checked_objects
    assert {blob, tree, recovered}.issubset(with_logs.checked_objects)
    assert {blob, tree, recovered}.issubset(with_logs.reachable)
    assert with_logs.ok


def test_installed_fsck_defaults_to_reflogs_and_no_reflogs_restores_unreachable(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)
    blob, tree, recovered = _commit(repo, b"recovered\n", message="recovered")
    _write_log(repo, "HEAD", [_record(current, recovered)])

    default = _run(repo, "fsck", "--unreachable")
    disabled = _run(repo, "fsck", "--unreachable", "--no-reflogs")

    assert default.returncode == 0, default.stderr.decode()
    assert recovered.encode() not in default.stdout
    assert blob.encode() not in default.stdout
    assert tree.encode() not in default.stdout

    assert disabled.returncode == 0, disabled.stderr.decode()
    assert f"unreachable commit {recovered}".encode() in disabled.stdout
    assert f"unreachable tree {tree}".encode() in disabled.stdout
    assert f"unreachable blob {blob}".encode() in disabled.stdout


def test_malformed_reflog_fails_closed_unless_reflogs_are_disabled(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)
    _write_log(repo, "HEAD", ["not a reflog record\n"])

    api = fsck(repo, include_reflogs=True)
    default = _run(repo, "fsck")
    disabled = _run(repo, "fsck", "--no-reflogs")

    assert any(issue.code == "bad-reflog" for issue in api.errors)
    assert default.returncode == 1
    assert b"bad-reflog" in default.stderr
    assert disabled.returncode == 0, disabled.stderr.decode()
    assert b"bad-reflog" not in disabled.stderr


def test_missing_reflog_target_is_a_connectivity_error_only_when_enabled(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _, _, current = _commit(repo, b"current\n", message="current")
    _publish(repo, current)
    missing = "a" * 64
    _write_log(repo, "HEAD", [_record(current, missing)])

    enabled = fsck(repo, connectivity_only=True, include_reflogs=True)
    disabled = fsck(repo, connectivity_only=True)

    assert any(issue.code == "object-read" and issue.oid == missing for issue in enabled.errors)
    assert not any(issue.oid == missing for issue in disabled.issues)


def test_installed_help_advertises_reflog_switch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = _run(repo, "fsck", "--help")

    assert result.returncode == 0
    assert b"--no-reflogs" in result.stdout
    assert b"--connectivity-only" in result.stdout
