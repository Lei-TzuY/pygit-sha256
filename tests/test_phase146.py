"""Phase 146 tests: fsck root and annotated-tag diagnostics."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck import fsck
from pygit.fsck_diagnostics import annotated_tags, root_commits
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(
    repo: Repository,
    *,
    tree: str,
    parents: list[str],
    message: str,
    timestamp: int,
) -> str:
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
    blob = repo.store.write(BlobObject(b"phase146\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    root = _commit(repo, tree=tree, parents=[], message="root", timestamp=1)
    tip = _commit(repo, tree=tree, parents=[root], message="tip", timestamp=2)
    orphan = _commit(repo, tree=tree, parents=[], message="orphan", timestamp=3)
    tag = repo.store.write(
        TagObject(
            target_sha=root,
            target_type=b"commit",
            tag_name="v1",
            tagger=Identity("Tagger", "tagger@example.com", 4, "+0000"),
            message="release",
        )
    )

    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    tag_ref = repo.pygit_dir / "refs" / "tags" / "v1"
    tag_ref.parent.mkdir(parents=True, exist_ok=True)
    tag_ref.write_text(tag + "\n", encoding="ascii")
    return repo, {
        "blob": blob,
        "tree": tree,
        "root": root,
        "tip": tip,
        "orphan": orphan,
        "tag": tag,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_root_api_reports_reachable_and_unreachable_root_commits(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    report = fsck(repo)

    assert root_commits(repo, report) == tuple(sorted((h["root"], h["orphan"])))


def test_root_cli_is_independent_of_dangling_suppression(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "fsck", "--root", "--no-dangling")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().splitlines() == [
        f"root {oid}" for oid in sorted((h["root"], h["orphan"]))
    ]


def test_root_cli_still_scans_all_objects_with_explicit_head(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "fsck", "--root", "--no-dangling", h["tip"])

    assert result.returncode == 0, result.stderr.decode()
    assert {line for line in result.stdout.decode().splitlines()} == {
        f"root {h['root']}",
        f"root {h['orphan']}",
    }


def test_shallow_commit_is_presented_as_synthetic_root(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    (repo.pygit_dir / "shallow").write_text(h["tip"] + "\n", encoding="ascii")

    report = fsck(repo)

    assert set(root_commits(repo, report)) == {h["root"], h["tip"], h["orphan"]}


def test_tag_api_reports_annotated_tag_relationship(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)
    report = fsck(repo)

    entries = annotated_tags(repo, report)

    assert len(entries) == 1
    assert entries[0].tag_oid == h["tag"]
    assert entries[0].target_oid == h["root"]
    assert entries[0].target_type == "commit"
    assert entries[0].tag_name == "v1"


def test_tags_cli_matches_native_relationship_shape(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "fsck", "--tags", "--no-dangling")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == (
        f"tagged commit {h['root']} (v1) in {h['tag']}\n".encode()
    )


def test_tags_cli_is_independent_of_explicit_reachability_heads(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "fsck", "--tags", "--no-dangling", h["tip"])

    assert result.returncode == 0, result.stderr.decode()
    assert f"tagged commit {h['root']} (v1) in {h['tag']}".encode() in result.stdout


def test_connectivity_only_suppresses_incomplete_root_and_tag_reports(tmp_path: Path) -> None:
    repo, _ = _graph(tmp_path)

    result = _run(repo, "fsck", "--connectivity-only", "--root", "--tags", "--no-dangling")

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b""


def test_root_and_tags_compose_before_dangling_output(tmp_path: Path) -> None:
    repo, h = _graph(tmp_path)

    result = _run(repo, "fsck", "--root", "--tags")

    assert result.returncode == 0, result.stderr.decode()
    lines = result.stdout.decode().splitlines()
    dangling_index = lines.index(f"dangling commit {h['orphan']}")
    assert all(lines.index(f"root {oid}") < dangling_index for oid in (h["root"], h["orphan"]))
    assert lines.index(f"tagged commit {h['root']} (v1) in {h['tag']}") < dangling_index


def test_installed_help_lists_root_and_tags(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "fsck", "--help")

    assert result.returncode == 0
    assert b"--root" in result.stdout
    assert b"--tags" in result.stdout
