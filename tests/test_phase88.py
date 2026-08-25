"""Cross-phase regression for Phase 86 PackReader and Phase 87 points-at."""

from pathlib import Path

from pygit import Repository, repack
from pygit.objects import CommitObject, Identity, TagObject, TreeObject
from pygit.ref_query import query_refs

IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def test_packed_nested_tag_points_at(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject())
    commit = repo.store.write(CommitObject(tree=tree, parents=[], author=IDENT, committer=IDENT, message="c"))
    inner = repo.store.write(TagObject(target_sha=commit, target_type=b"commit", tag_name="inner", tagger=IDENT, message="inner"))
    outer = repo.store.write(TagObject(target_sha=inner, target_type=b"tag", tag_name="outer", tagger=IDENT, message="outer"))
    repo.refs.set_tag("inner", inner)
    repo.refs.set_tag("outer", outer)

    packed = repack(repo, all_objects=True, delete_redundant=True)
    assert packed.object_count >= 4
    assert all(repo.store.exists(oid) for oid in (commit, inner, outer))

    assert [r.refname for r in query_refs(repo, points_at=[inner], sort_keys=["refname"])] == [
        "refs/tags/inner",
        "refs/tags/outer",
    ]
    assert [r.refname for r in query_refs(repo, points_at=[commit], sort_keys=["refname"])] == [
        "refs/tags/inner",
        "refs/tags/outer",
    ]
