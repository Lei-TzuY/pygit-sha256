"""Phase 61 hardening tests for merge-tree graph and binary edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from pygit import Repository, merge_tree
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject


def _commit(
    repo: Repository,
    data: bytes = b"same\n",
    *,
    parents: Sequence[str] = (),
    message: str = "commit",
) -> str:
    blob = repo.store.write(BlobObject(data))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "f.txt", blob)]))
    ident = Identity("Tester", "t@example.com", timestamp=1, timezone="+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=list(parents),
            author=ident,
            committer=ident,
            message=message,
        )
    )


def test_invalid_utf8_is_binary_not_lossy_text_merge(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, b"\xffbase\n", message="base")
    ours = _commit(repo, b"\xffours\n", parents=[base], message="ours")
    theirs = _commit(repo, b"\xfftheirs\n", parents=[base], message="theirs")

    objects_before = set(repo.store.all_shas())
    result = merge_tree(repo, ours, theirs)

    assert not result.clean
    assert result.tree_oid is None
    assert [(item.path, item.reason) for item in result.conflicts] == [("f.txt", "binary")]
    assert set(repo.store.all_shas()) == objects_before


def test_annotated_tag_commitish_is_peeled(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    base = _commit(repo, b"base\n", message="base")
    ours = _commit(repo, b"ours\n", parents=[base], message="ours")
    theirs = _commit(repo, b"base\n", parents=[base], message="theirs")
    ident = Identity("Tagger", "tagger@example.com", timestamp=2, timezone="+0000")
    tag_oid = repo.store.write(
        TagObject(
            target_sha=ours,
            target_type=b"commit",
            tag_name="ours-tag",
            tagger=ident,
            message="tag",
        )
    )
    repo.refs.set_tag("ours-tag", tag_oid)

    result = merge_tree(repo, "ours-tag", theirs)

    assert result.clean
    assert result.ours_oid == ours
    assert result.base_oid == base


def test_multiple_best_merge_bases_require_explicit_choice(tmp_path: Path) -> None:
    repo = Repository.init(str(tmp_path / "r"))
    root = _commit(repo, message="root")
    left = _commit(repo, parents=[root], message="left")
    right = _commit(repo, parents=[root], message="right")
    merge_left = _commit(repo, parents=[left, right], message="merge-left")
    merge_right = _commit(repo, parents=[right, left], message="merge-right")

    with pytest.raises(RuntimeError, match="multiple merge bases"):
        merge_tree(repo, merge_left, merge_right)

    explicit = merge_tree(repo, merge_left, merge_right, base=left)
    assert explicit.clean
    assert explicit.base_oid == left
