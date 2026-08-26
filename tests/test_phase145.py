"""Phase 145 tests: fsck reference consistency independent of reachability roots."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.fsck_references import verify_references
from pygit.objects import BlobObject, CommitObject, Identity, TreeEntry, TreeObject


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "r"))


def _commit(repo: Repository, payload: bytes, *, message: str, timestamp: int) -> str:
    blob = repo.store.write(BlobObject(payload))
    tree = repo.store.write(TreeObject([TreeEntry("100644", "file.txt", blob)]))
    identity = Identity("Tester", "tester@example.com", timestamp, "+0000")
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=identity,
            committer=identity,
            message=message,
        )
    )


def _publish(repo: Repository, commit: str) -> None:
    repo.refs.set_branch("main", commit)
    repo.refs.set_head_symbolic("main")


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_reference_verifier_accepts_clean_and_unborn_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    assert verify_references(repo) == ()

    commit = _commit(repo, b"published\n", message="published", timestamp=1)
    _publish(repo, commit)
    assert verify_references(repo) == ()


def test_explicit_head_still_checks_unrelated_malformed_ref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _commit(repo, b"head\n", message="head", timestamp=1)
    bad = repo.pygit_dir / "refs" / "heads" / "broken"
    bad.write_text("not-an-object\n", encoding="utf-8")

    result = _run(repo, "fsck", head)

    assert result.returncode == 1
    assert b"bad-reference" in result.stderr
    assert b"refs/heads/broken" in result.stderr


def test_no_references_opts_out_without_changing_explicit_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _commit(repo, b"head\n", message="head", timestamp=1)
    bad = repo.pygit_dir / "refs" / "heads" / "broken"
    bad.write_text("not-an-object\n", encoding="utf-8")

    result = _run(repo, "fsck", "--no-references", head)

    assert result.returncode == 0, result.stderr.decode()
    assert b"bad-reference" not in result.stderr
    assert head.encode() not in result.stdout


def test_symbolic_ref_cycle_is_reported_with_explicit_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _commit(repo, b"head\n", message="head", timestamp=1)
    a = repo.pygit_dir / "refs" / "heads" / "a"
    b = repo.pygit_dir / "refs" / "heads" / "b"
    a.write_text("ref: refs/heads/b\n", encoding="utf-8")
    b.write_text("ref: refs/heads/a\n", encoding="utf-8")

    issues = verify_references(repo)

    assert any(issue.code == "bad-symbolic-reference" for issue in issues)
    result = _run(repo, "fsck", head)
    assert result.returncode == 1
    assert b"bad-symbolic-reference" in result.stderr


def test_malformed_packed_refs_is_checked_independently(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _commit(repo, b"head\n", message="head", timestamp=1)
    (repo.pygit_dir / "packed-refs").write_text("definitely malformed\n", encoding="utf-8")

    checked = _run(repo, "fsck", head)
    skipped = _run(repo, "fsck", "--no-references", head)

    assert checked.returncode == 1
    assert b"bad-packed-refs" in checked.stderr
    assert skipped.returncode == 0, skipped.stderr.decode()


def test_packed_and_loose_namespace_conflict_is_reported(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = _commit(repo, b"head\n", message="head", timestamp=1)
    nested = repo.pygit_dir / "refs" / "heads" / "topic" / "child"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text(head + "\n", encoding="utf-8")
    (repo.pygit_dir / "packed-refs").write_text(
        "# pack-refs with: sorted\n" + f"{head} refs/heads/topic\n",
        encoding="utf-8",
    )

    issues = verify_references(repo)

    assert any(issue.code == "reference-namespace-conflict" for issue in issues)


def test_reference_error_prevents_lost_found_materialization(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    published = _commit(repo, b"published\n", message="published", timestamp=1)
    _publish(repo, published)
    rescue = _commit(repo, b"rescue\n", message="rescue", timestamp=2)
    (repo.pygit_dir / "refs" / "heads" / "broken").write_text(
        "not-an-object\n", encoding="utf-8"
    )

    result = _run(repo, "fsck", "--lost-found", rescue)

    assert result.returncode == 1
    assert f"dangling commit {published}".encode() in result.stdout
    assert not (repo.pygit_dir / "lost-found").exists()


def test_installed_help_lists_reference_opt_out(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "fsck", "--help")

    assert result.returncode == 0
    assert b"--no-references" in result.stdout
