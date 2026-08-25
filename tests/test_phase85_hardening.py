"""Focused hardening for Phase 85 ``for-each-ref --points-at`` semantics."""

from __future__ import annotations

from pathlib import Path

from pygit import Repository
from pygit.objects import CommitObject, Identity, TagObject, TreeObject
from pygit.ref_query import query_refs


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def test_points_at_matches_intermediate_objects_in_nested_tag_chain(tmp_path: Path) -> None:
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

    # Native Git treats every object in the annotated-tag peel chain as a
    # points-at match: outer -> inner -> commit.
    assert [
        record.refname
        for record in query_refs(repo, points_at=[inner], sort_keys=["refname"])
    ] == ["refs/tags/inner", "refs/tags/outer"]
    assert [
        record.refname
        for record in query_refs(repo, points_at=[commit], sort_keys=["refname"])
    ] == ["refs/tags/inner", "refs/tags/outer"]
