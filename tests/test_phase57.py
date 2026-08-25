"""Phase 57 tests: unified rev-parse and object-ish resolution."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from pygit import Repository
from pygit.objects import BlobObject, CommitObject, Identity, TagObject, TreeEntry, TreeObject
from pygit.packed_refs import pack_refs
from pygit.revision import (
    abbreviate_oid,
    glob_refs,
    namespace_refs,
    resolve_abbreviation,
    resolve_revision,
    symbolic_refname,
)


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, tree: str, parents: list[str], message: str, timestamp: int) -> str:
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _history(repo: Repository) -> dict[str, str]:
    root_blob = repo.store.write(BlobObject(b"root\n"))
    root_tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", root_blob)]))
    root = _commit(repo, root_tree, [], "root", 1)

    note_blob = repo.store.write(BlobObject(b"nested\n"))
    nested_tree = repo.store.write(TreeObject([TreeEntry("100644", "note.txt", note_blob)]))
    tip_blob = repo.store.write(BlobObject(b"tip\n"))
    tip_tree = repo.store.write(
        TreeObject(
            [
                TreeEntry("040000", "dir", nested_tree),
                TreeEntry("100644", "file.txt", tip_blob),
            ]
        )
    )
    tip = _commit(repo, tip_tree, [root], "tip", 2)

    side_blob = repo.store.write(BlobObject(b"side\n"))
    side_tree = repo.store.write(TreeObject([TreeEntry("100644", "side.txt", side_blob)]))
    side = _commit(repo, side_tree, [root], "side", 3)

    merge = _commit(repo, tip_tree, [tip, side], "merge", 4)
    repo.refs.set_branch("main", merge)
    repo.refs.set_branch("topic", side)
    repo.refs.set_head_symbolic("main")

    tag_oid = repo.store.write(
        TagObject(
            target_sha=tip,
            target_type=b"commit",
            tag_name="v1",
            tagger=Identity("Tagger", "tagger@example.com", 5, "+0000"),
            message="release",
        )
    )
    repo.refs.set_tag("v1", tag_oid)
    repo.refs.set_tag("light", root)

    return {
        "root": root,
        "root_tree": root_tree,
        "tip": tip,
        "tip_tree": tip_tree,
        "tip_blob": tip_blob,
        "note_blob": note_blob,
        "nested_tree": nested_tree,
        "side": side,
        "merge": merge,
        "tag": tag_oid,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class TestRevisionResolver:
    def test_resolves_refs_prefixes_ancestry_and_tree_paths(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)

        assert resolve_revision(repo, "HEAD") == h["merge"]
        assert resolve_revision(repo, h["merge"][:12]) == h["merge"]
        assert resolve_revision(repo, "HEAD~1") == h["tip"]
        assert resolve_revision(repo, "HEAD^2") == h["side"]
        assert resolve_revision(repo, "HEAD~1:file.txt") == h["tip_blob"]
        assert resolve_revision(repo, "HEAD:dir/note.txt") == h["note_blob"]
        assert resolve_revision(repo, "HEAD:") == h["tip_tree"]

    def test_typed_peeling_for_tags_commits_trees_and_blobs(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)

        assert resolve_revision(repo, "v1^{tag}") == h["tag"]
        assert resolve_revision(repo, "v1^{}") == h["tip"]
        assert resolve_revision(repo, "v1^{commit}") == h["tip"]
        assert resolve_revision(repo, "v1^{tree}") == h["tip_tree"]
        assert resolve_revision(repo, "HEAD~1:file.txt^{blob}") == h["tip_blob"]

        with pytest.raises(RuntimeError):
            resolve_revision(repo, "v1^{blob}")
        with pytest.raises(RuntimeError):
            resolve_revision(repo, "HEAD~1:file.txt^{tree}")

    def test_shallow_boundary_blocks_explicit_parent_walk(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)
        (repo.pygit_dir / "shallow").write_text(h["tip"] + "\n", encoding="utf-8")

        assert resolve_revision(repo, "HEAD~1") == h["tip"]
        with pytest.raises(ValueError, match="shallow boundary"):
            resolve_revision(repo, "HEAD~2")

    def test_abbreviated_ids_continue_to_work_after_repack(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)
        repo.repack(delete_loose=True)

        prefix = h["merge"][:12]
        assert resolve_abbreviation(repo, prefix) == h["merge"]
        assert resolve_revision(repo, prefix) == h["merge"]
        assert abbreviate_oid(repo, h["merge"], 8).startswith(prefix[:8])
        assert repo.store.read(h["merge"]).message == "merge"

    def test_symbolic_and_namespace_queries_use_packed_refs(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)
        pack_refs(repo, all_refs=True, prune=True)

        assert symbolic_refname(repo, "HEAD") == "refs/heads/main"
        assert symbolic_refname(repo, "main") == "refs/heads/main"
        assert namespace_refs(repo, "branches") == [
            (h["merge"], "refs/heads/main"),
            (h["side"], "refs/heads/topic"),
        ]
        assert namespace_refs(repo, "tags", "v*") == [(h["tag"], "refs/tags/v1")]
        assert glob_refs(repo, "refs/heads/t*") == [(h["side"], "refs/heads/topic")]

    def test_invalid_tree_paths_are_not_normalized_silently(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _history(repo)

        for expression in ("HEAD:/file.txt", "HEAD:dir//note.txt", "HEAD:../file.txt"):
            with pytest.raises(ValueError):
                resolve_revision(repo, expression)


class TestRevParseCLI:
    def test_multiple_revisions_and_typed_peeling(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)

        result = _run(repo, "rev-parse", "HEAD", "HEAD~1", "v1^{tree}")
        assert result.returncode == 0, result.stderr.decode()
        assert result.stdout.decode().splitlines() == [h["merge"], h["tip"], h["tip_tree"]]

    def test_verify_quiet_short_and_disambiguate(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)

        verified = _run(repo, "rev-parse", "--verify", "HEAD^{commit}")
        assert verified.returncode == 0
        assert verified.stdout.decode().strip() == h["merge"]

        missing = _run(repo, "rev-parse", "--verify", "--quiet", "does-not-exist")
        assert missing.returncode == 1
        assert missing.stderr == b""

        short = _run(repo, "rev-parse", "--short=12", "HEAD")
        assert short.returncode == 0
        assert short.stdout.decode().strip() == h["merge"][:12]

        matches = _run(repo, "rev-parse", f"--disambiguate={h['merge'][:8]}")
        assert matches.returncode == 0
        assert h["merge"] in matches.stdout.decode().splitlines()

    def test_symbolic_names_survive_pack_refs(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _history(repo)
        pack_refs(repo, all_refs=True, prune=True)

        full = _run(repo, "rev-parse", "--symbolic-full-name", "HEAD")
        short = _run(repo, "rev-parse", "--abbrev-ref", "HEAD")

        assert full.returncode == 0, full.stderr.decode()
        assert full.stdout == b"refs/heads/main\n"
        assert short.returncode == 0, short.stderr.decode()
        assert short.stdout == b"main\n"

    def test_namespace_and_glob_expansion(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)

        branches = _run(repo, "rev-parse", "--branches")
        assert branches.returncode == 0
        assert branches.stdout.decode().splitlines() == [h["merge"], h["side"]]

        tags = _run(repo, "rev-parse", "--tags=v*")
        assert tags.returncode == 0
        assert tags.stdout.decode().splitlines() == [h["tag"]]

        globbed = _run(repo, "rev-parse", "--glob=refs/heads/t*")
        assert globbed.returncode == 0
        assert globbed.stdout.decode().splitlines() == [h["side"]]

    def test_revision_filtering_negation_and_shell_quote(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        h = _history(repo)

        revs = _run(repo, "rev-parse", "--revs-only", "HEAD", "README.missing")
        paths = _run(repo, "rev-parse", "--no-revs", "HEAD", "README.missing")
        negated = _run(repo, "rev-parse", "--not", "HEAD")
        quoted = _run(repo, "rev-parse", "--sq", "HEAD")

        assert revs.stdout.decode().splitlines() == [h["merge"]]
        assert paths.stdout == b"README.missing\n"
        assert negated.stdout == f"^{h['merge']}\n".encode()
        assert quoted.stdout == f"'{h['merge']}'\n".encode()

    def test_repository_metadata_and_object_format(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _history(repo)

        object_format = _run(repo, "rev-parse", "--show-object-format")
        ref_format = _run(repo, "rev-parse", "--show-ref-format")
        git_dir = _run(repo, "rev-parse", "--path-format=relative", "--git-dir")
        top = _run(repo, "rev-parse", "--show-toplevel")

        assert object_format.stdout == b"sha256\n"
        assert ref_format.stdout == b"files\n"
        assert git_dir.stdout == b".pygit\n"
        assert top.stdout.decode().strip() == str(repo.worktree)

    def test_cat_file_and_checkout_index_runtime_routes_coexist(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _history(repo)

        tree = _run(repo, "cat-file", "-t", "v1^{tree}")
        blob = _run(repo, "cat-file", "-t", "HEAD~1:file.txt^{blob}")
        checkout_help = _run(repo, "checkout-index", "--help")

        assert tree.returncode == 0, tree.stderr.decode()
        assert tree.stdout == b"tree\n"
        assert blob.returncode == 0, blob.stderr.decode()
        assert blob.stdout == b"blob\n"
        assert checkout_help.returncode == 0
        assert b"checkout-index" in checkout_help.stdout
