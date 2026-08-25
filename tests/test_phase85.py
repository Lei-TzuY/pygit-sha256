"""Phase 85 tests: object-target filtering for ``for-each-ref --points-at``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, repack
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject
from pygit.packed_refs import pack_refs
from pygit.ref_query import query_refs


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _fixture(repo: Repository) -> dict[str, str]:
    blob = repo.store.write(BlobObject(b"payload\n"))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    c1 = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message="one",
        )
    )
    c2 = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[c1],
            author=IDENT,
            committer=IDENT,
            message="two",
        )
    )
    annotated = repo.store.write(
        TagObject(
            target_sha=c1,
            target_type=b"commit",
            tag_name="annotated",
            tagger=IDENT,
            message="annotated c1",
        )
    )
    blob_tag = repo.store.write(
        TagObject(
            target_sha=blob,
            target_type=b"blob",
            tag_name="blob-ann",
            tagger=IDENT,
            message="annotated blob",
        )
    )

    repo.refs.set_branch("main", c2)
    repo.refs.set_branch("old", c1)
    repo.refs.set_tag("light", c1)
    repo.refs.set_tag("annotated", annotated)
    repo.refs.set_tag("blob-direct", blob)
    repo.refs.set_tag("blob-ann", blob_tag)
    repo.refs.set_head_symbolic("main")
    return {
        "blob": blob,
        "tree": tree,
        "c1": c1,
        "c2": c2,
        "annotated": annotated,
        "blob_tag": blob_tag,
    }


def _names(records) -> list[str]:
    return [record.refname for record in records]


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "for-each-ref", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_points_at_matches_direct_and_peeled_annotated_tags(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    records = query_refs(repo, points_at=[ids["c1"]], sort_keys=["refname"])
    assert _names(records) == [
        "refs/heads/old",
        "refs/tags/annotated",
        "refs/tags/light",
    ]


def test_points_at_tag_object_matches_only_direct_tag_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    records = query_refs(repo, points_at=[ids["annotated"]])
    assert _names(records) == ["refs/tags/annotated"]


def test_repeated_points_at_targets_are_or_not_and(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    records = query_refs(
        repo,
        points_at=[ids["c1"], ids["c2"]],
        sort_keys=["refname"],
    )
    assert _names(records) == [
        "refs/heads/main",
        "refs/heads/old",
        "refs/tags/annotated",
        "refs/tags/light",
    ]


def test_points_at_uses_shared_objectish_resolver_for_tree_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    records = query_refs(repo, points_at=["main:file.txt"], sort_keys=["refname"])
    assert _names(records) == ["refs/tags/blob-ann", "refs/tags/blob-direct"]


def test_points_at_composes_before_sort_and_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    records = query_refs(
        repo,
        patterns=["refs/tags/"],
        points_at=[ids["c1"], ids["c2"]],
        sort_keys=["-refname"],
        count=1,
    )
    assert _names(records) == ["refs/tags/light"]


def test_points_at_survives_packed_refs_and_packed_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    pack_refs(repo, all_refs=True)
    result = repack(repo, all_objects=True, delete_redundant=True)
    assert result.object_count > 0
    assert not (repo.pygit_dir / "refs" / "heads" / "old").exists()
    assert not (repo.store.root / ids["c1"][:2] / ids["c1"][2:]).exists()

    records = query_refs(repo, points_at=[ids["c1"]], sort_keys=["refname"])
    assert _names(records) == [
        "refs/heads/old",
        "refs/tags/annotated",
        "refs/tags/light",
    ]


def test_installed_cli_supports_repeated_points_at_and_formatting(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    result = _run(
        repo,
        f"--points-at={ids['c1']}",
        "--points-at",
        ids["c2"],
        "--sort=refname",
        "--format=%(refname:short)",
        "refs/heads/",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["main", "old"]
    assert result.stderr == ""


def test_installed_cli_accepts_tree_path_objectish(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(
        repo,
        "--points-at=main:file.txt",
        "--sort=refname",
        "--format=%(refname)",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refs/tags/blob-ann", "refs/tags/blob-direct"]


def test_unknown_points_at_object_fails_cleanly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    with pytest.raises(KeyError):
        query_refs(repo, points_at=["definitely-missing"])

    result = _run(repo, "--points-at=definitely-missing")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "error:" in result.stderr


def test_help_exposes_points_at(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(repo, "--help")
    assert result.returncode == 0
    assert "--points-at" in result.stdout
