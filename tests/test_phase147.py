"""Phase 147 tests: fsck reachable-object name decoration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck import fsck
from pygit.fsck_names import reachable_object_names
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, tree: str, parents: list[str], message: str, timestamp: int) -> str:
    ident = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=ident,
            committer=ident,
            message=message,
        )
    )


def _graph(tmp_path: Path) -> tuple[Repository, dict[str, str]]:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"phase147\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    root = _commit(repo, tree, [], "root", 1)
    tip = _commit(repo, tree, [root], "tip", 2)
    orphan = _commit(repo, tree, [], "orphan", 3)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    return repo, {"blob": blob, "tree": tree, "root": root, "tip": tip, "orphan": orphan}


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_name_api_walks_commit_tree_blob_and_parent(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    report = fsck(repo)

    names = reachable_object_names(repo, report)

    assert names[h["tip"]] == "HEAD"
    assert names[h["root"]] == "HEAD~1"
    assert names[h["tree"]] == "HEAD^{tree}"
    assert names[h["blob"]] == "HEAD:file.txt"
    assert h["orphan"] not in names


def test_name_api_preserves_explicit_revision_spelling(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    report = fsck(repo, heads=["main~1"])

    names = reachable_object_names(repo, report)

    assert names[h["root"]] == "main~1"
    assert names[h["tree"]] == "main~1^{tree}"


def test_cli_decorates_reachable_object_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"bad-name\n"))
    bad_tree = repo.store.write(TreeObject([TreeEntry("100644", ".", blob)]))
    outer = repo.store.write(TreeObject([TreeEntry("040000", "src", bad_tree)]))
    tip = _commit(repo, outer, [], "tip", 1)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "fsck", "--name-objects", "--no-dangling")

    assert result.returncode == 1
    assert f"bad-tree-name {bad_tree} (HEAD:src)".encode() in result.stderr


def test_cli_without_name_objects_preserves_existing_error_format(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"bad-name\n"))
    bad_tree = repo.store.write(TreeObject([TreeEntry("100644", ".", blob)]))
    tip = _commit(repo, bad_tree, [], "tip", 1)
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    result = _run(repo, "fsck", "--no-dangling")

    assert result.returncode == 1
    assert f"bad-tree-name {bad_tree}:".encode() in result.stderr
    assert b"(HEAD" not in result.stderr


def test_cli_does_not_invent_names_for_unreachable_objects(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "fsck", "--name-objects")

    assert result.returncode == 0, result.stderr.decode()
    assert f"dangling commit {h['orphan']}\n".encode() in result.stdout
    assert f"dangling commit {h['orphan']} (".encode() not in result.stdout


def test_cli_name_objects_composes_with_tag_diagnostics(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    tag = repo.store.write(
        TagObject(
            target_sha=h["root"],
            target_type=b"commit",
            tag_name="v1",
            tagger=Identity("Tagger", "tagger@example.com", 4, "+0000"),
            message="release",
        )
    )
    tag_ref = repo.pygit_dir / "refs" / "tags" / "v1"
    tag_ref.parent.mkdir(parents=True, exist_ok=True)
    tag_ref.write_text(tag + "\n", encoding="ascii")

    result = _run(repo, "fsck", "--tags", "--name-objects", "--no-dangling")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (
        f"tagged commit {h['root']} (v1) in {tag} (refs/tags/v1)\n".encode()
    )


def test_cli_name_objects_composes_with_connectivity_only(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "fsck", "--connectivity-only", "--name-objects", "--no-dangling")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b""
    assert result.stderr == b""


def test_installed_help_lists_name_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "fsck", "--help")

    assert result.returncode == 0
    assert b"--name-objects" in result.stdout
