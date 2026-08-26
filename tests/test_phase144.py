"""Phase 144 tests: explicit fsck reachability heads and --cache composition."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck import fsck
from pygit.index import IndexEntry
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, payload: bytes, *, message: str, timestamp: int) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
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


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_api_explicit_head_replaces_implicit_ref_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, published = _commit(repo, b"published\n", message="published", timestamp=1)
    _publish(repo, published)
    orphan_blob, orphan_tree, orphan = _commit(repo, b"orphan\n", message="orphan", timestamp=2)

    report = fsck(repo, heads=[orphan])

    assert report.ok
    assert report.roots == {f"argument:1:{orphan}": orphan}
    assert {orphan_blob, orphan_tree, orphan}.issubset(report.reachable)
    assert published in report.unreachable
    assert published in report.dangling


def test_explicit_head_accepts_revision_names_and_multiple_heads(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, left = _commit(repo, b"left\n", message="left", timestamp=1)
    _, _, right = _commit(repo, b"right\n", message="right", timestamp=2)
    repo.refs.set_branch("left", left)
    repo.refs.set_branch("right", right)
    repo.refs.set_head_symbolic("left")

    report = fsck(repo, heads=["left", "right"])

    assert report.ok
    assert set(report.roots.values()) == {left, right}
    assert left in report.reachable
    assert right in report.reachable
    assert not report.dangling


def test_explicit_head_excludes_index_unless_cache_is_requested(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, head = _commit(repo, b"head\n", message="head", timestamp=1)
    staged = repo.store.write(BlobObject(b"staged\n"))
    repo.index.entries = {"staged.txt": IndexEntry("staged.txt", staged, "100644")}
    repo.index.save()

    plain = fsck(repo, heads=[head])
    cached = fsck(repo, heads=[head], include_index=True)

    assert staged in plain.unreachable
    assert staged in plain.dangling
    assert staged in cached.reachable
    assert staged not in cached.unreachable
    assert any(source == "index:staged.txt" for source in cached.roots)


def test_connectivity_only_explicit_head_visits_only_requested_closure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    published_blob, published_tree, published = _commit(
        repo, b"published\n", message="published", timestamp=1
    )
    _publish(repo, published)
    orphan_blob, orphan_tree, orphan = _commit(repo, b"orphan\n", message="orphan", timestamp=2)

    report = fsck(repo, heads=[orphan], connectivity_only=True)

    assert report.ok
    assert report.checked_objects == {orphan_blob, orphan_tree, orphan}
    assert published not in report.checked_objects
    assert published_tree not in report.checked_objects
    assert published_blob not in report.checked_objects
    assert report.unreachable == set()
    assert report.dangling == set()


def test_bad_explicit_head_is_a_failing_root_diagnostic(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, published = _commit(repo, b"published\n", message="published", timestamp=1)
    _publish(repo, published)

    report = fsck(repo, heads=["definitely-not-an-object"])

    assert not report.ok
    assert any(issue.code == "bad-root" and issue.source == "argument:1:definitely-not-an-object" for issue in report.errors)
    assert published in report.unreachable


def test_cli_explicit_head_changes_dangling_view(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, published = _commit(repo, b"published\n", message="published", timestamp=1)
    _publish(repo, published)
    _, _, orphan = _commit(repo, b"orphan\n", message="orphan", timestamp=2)

    default = _run(repo, "fsck")
    explicit = _run(repo, "fsck", orphan)

    assert default.returncode == 0, default.stderr.decode()
    assert f"dangling commit {orphan}".encode() in default.stdout
    assert explicit.returncode == 0, explicit.stderr.decode()
    assert f"dangling commit {published}".encode() in explicit.stdout
    assert f"dangling commit {orphan}".encode() not in explicit.stdout


def test_cli_cache_adds_index_to_explicit_head_set(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, head = _commit(repo, b"head\n", message="head", timestamp=1)
    staged = repo.store.write(BlobObject(b"staged\n"))
    repo.index.entries = {"staged.txt": IndexEntry("staged.txt", staged, "100644")}
    repo.index.save()

    plain = _run(repo, "fsck", head)
    cached = _run(repo, "fsck", "--cache", head)

    assert plain.returncode == 0, plain.stderr.decode()
    assert f"dangling blob {staged}".encode() in plain.stdout
    assert cached.returncode == 0, cached.stderr.decode()
    assert staged.encode() not in cached.stdout


def test_cli_explicit_head_composes_with_lost_found(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, _, published = _commit(repo, b"published\n", message="published", timestamp=1)
    _publish(repo, published)
    _, _, rescue = _commit(repo, b"rescue\n", message="rescue", timestamp=2)

    result = _run(repo, "fsck", "--lost-found", rescue)

    assert result.returncode == 0, result.stderr.decode()
    assert f"dangling commit {published}".encode() in result.stdout
    assert (repo.pygit_dir / "lost-found" / "commit" / published).read_text() == published + "\n"
    assert not (repo.pygit_dir / "lost-found" / "commit" / rescue).exists()


def test_installed_help_advertises_explicit_objects_and_cache(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "fsck", "--help")

    assert result.returncode == 0
    assert b"OBJECT" in result.stdout
    assert b"--cache" in result.stdout
