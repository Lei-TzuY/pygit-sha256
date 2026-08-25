"""Phase 78 tests: numeric reflog selectors in the shared revision resolver."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Optional

import pytest

from pygit import Repository, repack
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject
from pygit.revision import resolve_revision, symbolic_refname


ZERO = "0" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(
    repo: Repository,
    payload: bytes,
    parent: Optional[str],
    timestamp: int,
) -> tuple[str, str, str]:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[] if parent is None else [parent],
            author=identity,
            committer=identity,
            message=f"commit-{timestamp}",
        )
    )
    return commit, tree, blob


def _line(old: str, new: str, timestamp: int, message: str) -> str:
    return f"{old} {new} Tester <tester@example.com> {timestamp} +0000\t{message}\n"


def _write_log(repo: Repository, ref: str, text: str) -> Path:
    relative = "HEAD" if ref == "HEAD" else ref
    path = repo.pygit_dir / "logs" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _history(repo: Repository) -> dict[str, str]:
    root, root_tree, root_blob = _commit(repo, b"root\n", None, 100)
    middle, middle_tree, middle_blob = _commit(repo, b"middle\n", root, 200)
    tip, tip_tree, tip_blob = _commit(repo, b"tip\n", middle, 300)

    repo.refs.set_branch("main", tip)
    repo.refs.set_head_symbolic("main")
    log = (
        _line(ZERO, root, 100, "root")
        + _line(root, middle, 200, "middle")
        + _line(middle, tip, 300, "tip")
    )
    _write_log(repo, "HEAD", log)
    _write_log(repo, "refs/heads/main", log)
    return {
        "root": root,
        "root_tree": root_tree,
        "root_blob": root_blob,
        "middle": middle,
        "middle_tree": middle_tree,
        "middle_blob": middle_blob,
        "tip": tip,
        "tip_tree": tip_tree,
        "tip_blob": tip_blob,
    }


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_numeric_selectors_resolve_head_short_and_full_ref_names(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _history(repo)

    assert resolve_revision(repo, "HEAD@{0}") == h["tip"]
    assert resolve_revision(repo, "HEAD@{1}") == h["middle"]
    assert resolve_revision(repo, "HEAD@{2}") == h["root"]
    assert resolve_revision(repo, "main@{1}") == h["middle"]
    assert resolve_revision(repo, "refs/heads/main@{2}") == h["root"]

    assert symbolic_refname(repo, "HEAD@{0}") is None
    assert symbolic_refname(repo, "main@{1}") is None


def test_selectors_compose_with_ancestry_peeling_and_tree_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _history(repo)

    assert resolve_revision(repo, "HEAD@{0}~1") == h["middle"]
    assert resolve_revision(repo, "HEAD@{1}^0") == h["middle"]
    assert resolve_revision(repo, "HEAD@{1}^{tree}") == h["middle_tree"]
    assert resolve_revision(repo, "HEAD@{2}:file.txt") == h["root_blob"]
    assert resolve_revision(repo, "main@{1}:file.txt^{blob}") == h["middle_blob"]


def test_existing_commands_inherit_shared_selector_resolution(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _history(repo)

    rev_parse = _run(repo, "rev-parse", "HEAD@{1}")
    assert rev_parse.returncode == 0, rev_parse.stderr.decode()
    assert rev_parse.stdout == f"{h['middle']}\n".encode()

    cat_file = _run(repo, "cat-file", "-t", "main@{2}:file.txt")
    assert cat_file.returncode == 0, cat_file.stderr.decode()
    assert cat_file.stdout == b"blob\n"

    ls_tree = _run(repo, "ls-tree", "--name-only", "HEAD@{1}")
    assert ls_tree.returncode == 0, ls_tree.stderr.decode()
    assert ls_tree.stdout == b"file.txt\n"


def test_reflog_selectors_continue_to_resolve_packed_only_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _history(repo)

    result = repack(repo, all_objects=True, delete_redundant=True)
    assert result.object_count > 0
    loose_middle = repo.store.root / h["middle"][:2] / h["middle"][2:]
    assert not loose_middle.exists()

    assert resolve_revision(repo, "HEAD@{1}") == h["middle"]
    assert resolve_revision(repo, "HEAD@{1}^{tree}") == h["middle_tree"]


def test_selector_index_and_syntax_fail_loudly(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _history(repo)

    with pytest.raises(KeyError, match="out of range"):
        resolve_revision(repo, "HEAD@{3}")
    with pytest.raises(ValueError, match="numeric reflog selectors"):
        resolve_revision(repo, "HEAD@{-1}")
    with pytest.raises(ValueError, match="numeric reflog selectors"):
        resolve_revision(repo, "HEAD@{yesterday}")
    with pytest.raises(ValueError, match="Invalid reflog selector"):
        resolve_revision(repo, "@{0}")


def test_missing_reflog_zero_oid_and_missing_object_are_not_valid_revisions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _history(repo)

    with pytest.raises(KeyError, match="out of range"):
        resolve_revision(repo, "missing@{0}")

    _write_log(repo, "refs/heads/zero", _line(h["tip"], ZERO, 400, "deleted"))
    with pytest.raises(KeyError, match="zero object"):
        resolve_revision(repo, "zero@{0}")

    missing_oid = "f" * 64
    _write_log(repo, "refs/heads/ghost", _line(h["tip"], missing_oid, 500, "ghost"))
    with pytest.raises(KeyError, match="missing object"):
        resolve_revision(repo, "ghost@{0}")


def test_short_selector_preserves_reflog_path_fail_closed_semantics(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    h = _history(repo)
    outside = tmp_path / "outside.log"
    outside.write_text(_line(ZERO, h["tip"], 300, "outside"), encoding="utf-8")
    link = repo.pygit_dir / "logs" / "refs" / "heads" / "linked"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(RuntimeError, match="symbolic-link"):
        resolve_revision(repo, "linked@{0}")

    cli = _run(repo, "rev-parse", "--verify", "linked@{0}")
    assert cli.returncode == 1
    assert b"symbolic-link" in cli.stderr
