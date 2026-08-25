"""Phase 92 tests: preserve native empty-vs-blank stdin pattern semantics."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import CommitObject, Identity, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    repo = Repository.init(str(tmp_path / "repo"))
    tree = repo.store.write(TreeObject())
    commit = repo.store.write(
        CommitObject(
            tree=tree,
            parents=[],
            author=IDENT,
            committer=IDENT,
            message="tip",
        )
    )
    repo.refs.set_branch("main", commit)
    repo.refs.set_tag("v1", commit)
    repo.refs.set_head_symbolic("main")
    return repo


def _run(repo: Repository, input_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pygit",
            "for-each-ref",
            "--stdin",
            "--sort=refname",
            "--format=%(refname)",
        ],
        cwd=repo.worktree,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_empty_stdin_means_no_patterns_and_lists_all_refs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(repo, "")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refs/heads/main", "refs/tags/v1"]
    assert result.stderr == ""


def test_blank_only_stdin_is_a_nonmatching_pattern_record(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    for input_text in ("\n", "\r\n", "\n\n"):
        result = _run(repo, input_text)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert result.stderr == ""
