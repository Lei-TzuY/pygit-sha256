"""Graph-edge regression coverage for Phase 58 diff-tree."""

from pathlib import Path

from pygit.diff_plumbing import diff_tree
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject
from pygit.repo import Repository


def test_single_diff_tree_peels_annotated_tag_and_honors_shallow_boundary(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    ident = Identity("Tester", "tester@example.com", 1, "+0000")

    old_blob = repo.store.write(BlobObject(b"old\n"))
    old_tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", old_blob)]))
    root = repo.store.write(
        CommitObject(tree=old_tree, parents=[], author=ident, committer=ident, message="root")
    )

    new_blob = repo.store.write(BlobObject(b"new\n"))
    new_tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", new_blob)]))
    tip = repo.store.write(
        CommitObject(tree=new_tree, parents=[root], author=ident, committer=ident, message="tip")
    )
    tag = repo.store.write(
        TagObject(
            target_sha=tip,
            target_type=b"commit",
            tag_name="v1",
            tagger=ident,
            message="release",
        )
    )
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    repo.refs.set_tag("v1", tag)

    tagged = diff_tree(repo, "v1")
    assert [(entry.path, entry.status) for entry in tagged] == [("file.txt", "M")]

    (repo.pygit_dir / "shallow").write_text(tip + "\n", encoding="utf-8")
    assert diff_tree(repo, "HEAD") == []
    shallow_root = diff_tree(repo, "HEAD", root=True)
    assert [(entry.path, entry.status) for entry in shallow_root] == [("file.txt", "A")]
