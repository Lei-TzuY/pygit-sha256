"""Phase 90 tests: for-each-ref inclusion/exclusion and stdin pattern plumbing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository
from pygit.objects import CommitObject, Identity, TreeObject
from pygit.packed_refs import pack_refs
from pygit.ref_query import query_refs, read_ref_patterns


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, parents: list[str], message: str) -> str:
    tree = repo.store.write(TreeObject())
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=parents,
            author=IDENT,
            committer=IDENT,
            message=message,
        )
    )


def _fixture(repo: Repository) -> dict[str, str]:
    root = _commit(repo, [], "root")
    tip = _commit(repo, [root], "tip")
    other = _commit(repo, [root], "other")

    repo.refs.set_branch("main", tip)
    repo.refs.set_branch("release/v1", tip)
    repo.refs.set_branch("release/v2", tip)
    repo.refs.set_branch("topic", other)
    repo.refs.set_tag("v1", tip)
    repo.refs.set_remote("origin", "main", tip)
    repo.refs.set_head_symbolic("main")
    return {"root": root, "tip": tip, "other": other}


def _names(records) -> list[str]:
    return [record.refname for record in records]


def _run(
    repo: Repository,
    *args: str,
    input_text: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "for-each-ref", *args],
        cwd=repo.worktree,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_api_exclude_supports_literal_prefix_and_glob(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    literal = query_refs(repo, exclude_patterns=["refs/heads/release"])
    assert "refs/heads/release/v1" not in _names(literal)
    assert "refs/heads/release/v2" not in _names(literal)
    assert "refs/heads/main" in _names(literal)

    globbed = query_refs(repo, exclude_patterns=["refs/heads/release/*", "refs/tags/*"])
    names = _names(globbed)
    assert "refs/heads/release/v1" not in names
    assert "refs/heads/release/v2" not in names
    assert "refs/tags/v1" not in names
    assert "refs/remotes/origin/main" in names


def test_exclude_uses_full_ref_semantics_not_tail_matching(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    records = query_refs(repo, exclude_patterns=["main"])
    names = _names(records)
    assert "refs/heads/main" in names
    assert "refs/remotes/origin/main" in names


def test_include_then_exclude_composes_before_sort_and_count(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    records = query_refs(
        repo,
        patterns=["refs/heads/"],
        exclude_patterns=["refs/heads/release/*"],
        sort_keys=["refname"],
        count=2,
    )
    assert _names(records) == ["refs/heads/main", "refs/heads/topic"]


def test_exclude_composes_with_points_at(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    records = query_refs(
        repo,
        points_at=[ids["tip"]],
        exclude_patterns=["refs/heads/release/*"],
        sort_keys=["refname"],
    )
    assert _names(records) == [
        "refs/heads/main",
        "refs/remotes/origin/main",
        "refs/tags/v1",
    ]


def test_excluded_broken_object_ref_is_not_loaded(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    missing = "f" * 64
    repo.refs.set_branch("broken", missing)

    with pytest.raises(KeyError):
        query_refs(repo, patterns=["refs/heads/broken"])

    assert query_refs(
        repo,
        patterns=["refs/heads/"],
        exclude_patterns=["refs/heads/broken"],
    )


def test_exclusion_works_after_pack_refs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    pack_refs(repo, all_refs=True)

    records = query_refs(
        repo,
        patterns=["refs/heads/"],
        exclude_patterns=["refs/heads/release/*"],
        sort_keys=["refname"],
    )
    assert _names(records) == ["refs/heads/main", "refs/heads/topic"]


def test_read_ref_patterns_ignores_blank_records_but_preserves_spaces() -> None:
    assert read_ref_patterns([
        "\n",
        "refs/heads/\n",
        "  refs/tags/  \r\n",
        "\r\n",
        "refs/remotes/*\n",
    ]) == ["refs/heads/", "  refs/tags/  ", "refs/remotes/*"]


def test_installed_cli_exclude_and_no_exclude_reset(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    excluded = _run(
        repo,
        "--format=%(refname)",
        "--exclude=refs/heads/release/*",
        "refs/heads/",
    )
    assert excluded.returncode == 0, excluded.stderr
    assert excluded.stdout.splitlines() == ["refs/heads/main", "refs/heads/topic"]

    reset = _run(
        repo,
        "--format=%(refname)",
        "--exclude=refs/heads/release/*",
        "--no-exclude",
        "refs/heads/",
    )
    assert reset.returncode == 0, reset.stderr
    assert reset.stdout.splitlines() == [
        "refs/heads/main",
        "refs/heads/release/v1",
        "refs/heads/release/v2",
        "refs/heads/topic",
    ]


def test_installed_cli_stdin_patterns_ignore_blank_lines_and_accept_globs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(
        repo,
        "--stdin",
        "--format=%(refname)",
        input_text="\nrefs/heads/release/*\nrefs/tags/\n\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "refs/heads/release/v1",
        "refs/heads/release/v2",
        "refs/tags/v1",
    ]


def test_stdin_patterns_compose_with_exclude(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(
        repo,
        "--stdin",
        "--exclude=refs/heads/release/v2",
        "--format=%(refname)",
        input_text="refs/heads/release/*\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["refs/heads/release/v1"]


def test_stdin_rejects_positional_patterns_before_consuming_input(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(
        repo,
        "--stdin",
        "refs/heads/",
        input_text="refs/tags/\n",
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "--stdin cannot be combined with positional patterns" in result.stderr


def test_help_exposes_pattern_selection_options(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)

    result = _run(repo, "--help")
    assert result.returncode == 0, result.stderr
    assert "--exclude" in result.stdout
    assert "--no-exclude" in result.stdout
    assert "--stdin" in result.stdout
