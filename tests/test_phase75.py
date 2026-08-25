"""Phase 75 tests: revision-aware, typed ``ls-tree`` plumbing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, format_ls_tree, ls_tree, repack
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, tree: str, message: str = "commit") -> str:
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message=message,
        )
    )


def _fixture(repo: Repository) -> dict[str, str]:
    plain = repo.store.write(BlobObject(b"plain\n"))
    executable = repo.store.write(BlobObject(b"#!/bin/sh\n"))
    symlink = repo.store.write(BlobObject(b"file.txt"))
    nested_blob = repo.store.write(BlobObject(b"nested\n"))
    nested_tree = repo.store.write(
        TreeObject([TreeEntry("100644", "nested.txt", nested_blob)])
    )
    gitlink_tree = repo.store.write(TreeObject([]))
    gitlink_commit = _commit(repo, gitlink_tree, "gitlink")
    root = repo.store.write(
        TreeObject(
            [
                TreeEntry("100644", "file.txt", plain),
                TreeEntry("100755", "run.sh", executable),
                TreeEntry("120000", "link", symlink),
                TreeEntry("040000", "sub", nested_tree),
                TreeEntry("160000", "module", gitlink_commit),
            ]
        )
    )
    commit = _commit(repo, root)
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")
    tag = repo.store.write(
        TagObject(
            target_sha=commit,
            target_type=b"commit",
            tag_name="v1",
            tagger=IDENT,
            message="annotated",
        )
    )
    repo.refs.set_tag("v1", tag)
    return {
        "plain": plain,
        "executable": executable,
        "symlink": symlink,
        "nested_blob": nested_blob,
        "nested_tree": nested_tree,
        "gitlink_commit": gitlink_commit,
        "root": root,
        "commit": commit,
        "tag": tag,
    }


def test_default_listing_reports_sha256_modes_types_and_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    entries = ls_tree(repo, "HEAD")

    assert [(entry.mode, entry.object_type, entry.path) for entry in entries] == [
        ("100644", "blob", "file.txt"),
        ("120000", "blob", "link"),
        ("160000", "commit", "module"),
        ("100755", "blob", "run.sh"),
        ("040000", "tree", "sub"),
    ]
    assert entries[0].oid == ids["plain"]
    assert entries[2].oid == ids["gitlink_commit"]
    assert entries[4].oid == ids["nested_tree"]

    rendered = format_ls_tree(repo, entries).decode("utf-8")
    assert f"100644 blob {ids['plain']}\tfile.txt\n" in rendered
    assert f"160000 commit {ids['gitlink_commit']}\tmodule\n" in rendered
    assert f"040000 tree {ids['nested_tree']}\tsub\n" in rendered


def test_recursive_directory_and_show_tree_modes_are_distinct(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    recursive = ls_tree(repo, recursive=True)
    assert [entry.path for entry in recursive] == [
        "file.txt",
        "link",
        "module",
        "run.sh",
        "sub/nested.txt",
    ]

    with_trees = ls_tree(repo, recursive=True, show_trees=True)
    assert [entry.path for entry in with_trees] == [
        "file.txt",
        "link",
        "module",
        "run.sh",
        "sub",
        "sub/nested.txt",
    ]

    directories = ls_tree(repo, recursive=True, directories_only=True)
    assert [(entry.object_type, entry.path) for entry in directories] == [
        ("tree", "sub"),
    ]


def test_shared_revision_resolver_handles_tags_short_sha_and_rev_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    assert [entry.path for entry in ls_tree(repo, "v1")] == [
        "file.txt",
        "link",
        "module",
        "run.sh",
        "sub",
    ]
    subtree = ls_tree(repo, f"{ids['commit'][:12]}:sub")
    assert [(entry.path, entry.oid) for entry in subtree] == [
        ("nested.txt", ids["nested_blob"]),
    ]
    direct_tree = ls_tree(repo, ids["root"][:12])
    assert [entry.path for entry in direct_tree][-1] == "sub"


def test_packed_only_abbreviated_treeish_uses_shared_object_resolution(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    result = repack(repo, all_objects=True, delete_redundant=True)
    assert result.object_count > 0
    loose_commit = repo.store.root / ids["commit"][:2] / ids["commit"][2:]
    assert not loose_commit.exists()

    entries = ls_tree(repo, ids["commit"][:12])
    assert entries[0].oid == ids["plain"]
    assert repo.store.read(ids["plain"]).hash() == ids["plain"]


def test_nested_literal_pathspec_uses_minimum_nonrecursive_traversal(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    selected = ls_tree(repo, "HEAD", patterns=("sub/nested.txt",))
    assert [(entry.path, entry.oid) for entry in selected] == [
        ("sub/nested.txt", ids["nested_blob"]),
    ]

    directory = ls_tree(repo, "HEAD", patterns=("sub",))
    assert [(entry.object_type, entry.path) for entry in directory] == [
        ("tree", "sub"),
    ]


def test_recursive_pathspec_skips_unrelated_broken_subtrees(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    wanted_blob = repo.store.write(BlobObject(b"wanted\n"))
    wanted_tree = repo.store.write(
        TreeObject([TreeEntry("100644", "nested.txt", wanted_blob)])
    )
    not_a_tree = repo.store.write(BlobObject(b"broken child"))
    root = repo.store.write(
        TreeObject(
            [
                TreeEntry("040000", "broken", not_a_tree),
                TreeEntry("040000", "wanted", wanted_tree),
            ]
        )
    )

    selected = ls_tree(
        repo,
        root,
        recursive=True,
        patterns=("wanted/nested.txt",),
    )
    assert [(entry.path, entry.oid) for entry in selected] == [
        ("wanted/nested.txt", wanted_blob),
    ]

    with pytest.raises(RuntimeError, match="non-tree"):
        ls_tree(repo, root, patterns=("broken/file.txt",))


def test_glob_pathspec_and_invalid_pathspec_validation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    selected = ls_tree(repo, "HEAD", patterns=("sub/*.txt",))
    assert [(entry.path, entry.oid) for entry in selected] == [
        ("sub/nested.txt", ids["nested_blob"]),
    ]

    with pytest.raises(ValueError, match="pathspec"):
        ls_tree(repo, patterns=("../outside",))
    with pytest.raises(ValueError, match="pathspec"):
        ls_tree(repo, patterns=("/absolute",))


def test_format_modes_custom_atoms_abbreviation_and_nul_output(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = ls_tree(repo, patterns=("file.txt",))

    assert format_ls_tree(repo, entries, name_only=True) == b"file.txt\n"

    object_only = format_ls_tree(repo, entries, object_only=True, abbrev=8)
    abbreviated = object_only.decode("ascii").strip()
    assert len(abbreviated) >= 8
    assert ids["plain"].startswith(abbreviated)

    custom = format_ls_tree(
        repo,
        entries,
        format_string="%(objecttype):%(path):%%",
        nul_terminated=True,
    )
    assert custom == b"blob:file.txt:%\x00"

    with pytest.raises(ValueError, match="format atom"):
        format_ls_tree(repo, entries, format_string="%(unknown)")
    with pytest.raises(ValueError, match="mutually exclusive"):
        format_ls_tree(repo, entries, name_only=True, object_only=True)


def test_non_treeish_and_invalid_tree_entries_fail_cleanly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blob = repo.store.write(BlobObject(b"not a tree"))
    with pytest.raises(RuntimeError, match="not a tree-ish"):
        ls_tree(repo, blob)

    invalid_mode_tree = repo.store.write(
        TreeObject([TreeEntry("999999", "bad", blob)])
    )
    with pytest.raises(ValueError, match="unsupported tree entry mode"):
        ls_tree(repo, invalid_mode_tree)

    invalid_name_tree = repo.store.write(
        TreeObject([TreeEntry("100644", "../bad", blob)])
    )
    with pytest.raises(ValueError, match="invalid tree entry name"):
        ls_tree(repo, invalid_name_tree)


def test_installed_cli_routes_modern_ls_tree_without_legacy_parser(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pygit",
            "ls-tree",
            "-r",
            "--name-only",
            ids["commit"][:12],
            "--",
            "sub/nested.txt",
        ],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "sub/nested.txt\n"

    object_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pygit",
            "ls-tree",
            "--object-only",
            "--abbrev=8",
            "HEAD",
            "--",
            "file.txt",
        ],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert object_result.returncode == 0, object_result.stderr
    shown = object_result.stdout.strip()
    assert len(shown) >= 8
    assert ids["plain"].startswith(shown)
