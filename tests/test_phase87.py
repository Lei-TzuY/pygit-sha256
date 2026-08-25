"""Phase 87 regression tests for nested annotated-tag ``--points-at`` matching."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import CommitObject, Identity, TagObject, TreeObject
from pygit.ref_query import query_refs


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> tuple[Repository, str, str, str]:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject())
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message="commit",
        )
    )
    inner = repo.store.write(
        TagObject(
            target_sha=commit,
            target_type=b"commit",
            tag_name="inner",
            tagger=IDENT,
            message="inner",
        )
    )
    outer = repo.store.write(
        TagObject(
            target_sha=inner,
            target_type=b"tag",
            tag_name="outer",
            tagger=IDENT,
            message="outer",
        )
    )
    repo.refs.set_tag("inner", inner)
    repo.refs.set_tag("outer", outer)
    return repo, commit, inner, outer


def _names(repo: Repository, target: str) -> list[str]:
    return [
        record.refname
        for record in query_refs(repo, points_at=[target], sort_keys=["refname"])
    ]


def test_points_at_matches_intermediate_nested_tag_target(tmp_path: Path) -> None:
    repo, _, inner, _ = _repo(tmp_path)

    # outer -> inner -> commit: native Git matches both refs for --points-at=inner.
    assert _names(repo, inner) == ["refs/tags/inner", "refs/tags/outer"]


def test_points_at_matches_final_target_through_multiple_tags(tmp_path: Path) -> None:
    repo, commit, _, _ = _repo(tmp_path)

    assert _names(repo, commit) == ["refs/tags/inner", "refs/tags/outer"]


def test_installed_cli_matches_intermediate_nested_tag_target(tmp_path: Path) -> None:
    repo, _, inner, _ = _repo(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pygit",
            "for-each-ref",
            f"--points-at={inner}",
            "--sort=refname",
            "--format=%(refname)",
        ],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refs/tags/inner", "refs/tags/outer"]
    assert result.stderr == ""
