"""Integration coverage for Phase 58 with packed-only object IDs."""

from pathlib import Path

from pygit.diff_plumbing import diff_tree
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.repo import Repository


def test_diff_tree_resolves_packed_only_abbreviations(tmp_path: Path) -> None:
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
    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")

    repo.repack(delete_loose=True)
    assert not (repo.store.root / root[:2] / root[2:]).exists()
    assert not (repo.store.root / tip[:2] / tip[2:]).exists()

    entries = diff_tree(repo, root[:12], tip[:12])
    assert [(entry.path, entry.status) for entry in entries] == [("file.txt", "M")]
    assert entries[0].old_oid == old_blob
    assert entries[0].new_oid == new_blob
