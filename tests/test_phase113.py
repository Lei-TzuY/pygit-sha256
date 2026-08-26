"""Phase 113 tests: pack-refs include/exclude pattern selection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pygit import Repository
from pygit.objects import CommitObject, Identity, TreeObject
from pygit.packed_refs import pack_refs, read_packed_refs


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, parents: list[str], message: str, timestamp: int) -> str:
    tree = repo.store.write(TreeObject())
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


def _history(repo: Repository) -> tuple[str, str]:
    root = _commit(repo, [], "root", 1)
    tip = _commit(repo, [root], "tip", 2)
    repo.refs.set_branch("main", tip)
    return root, tip


def _run(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_include_replaces_default_tag_selection(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    repo.refs.set_branch("feature", tip)
    repo.refs.set_tag("v1", tip)

    packed = pack_refs(repo, includes=["refs/heads/f*"])

    assert [record.refname for record in packed] == ["refs/heads/feature"]
    records = read_packed_refs(repo.pygit_dir)
    assert set(records) == {"refs/heads/feature"}
    assert not (repo.pygit_dir / "refs" / "heads" / "feature").exists()
    assert (repo.pygit_dir / "refs" / "heads" / "main").is_file()
    assert (repo.pygit_dir / "refs" / "tags" / "v1").is_file()


def test_repeated_include_union_and_exclude_wins(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    repo.refs.set_branch("topic/one", tip)
    repo.refs.set_branch("topic/two", tip)
    repo.refs.set_tag("v1", tip)
    repo.refs.set_tag("release", tip)

    packed = pack_refs(
        repo,
        includes=["refs/heads/topic/*", "refs/tags/v?"],
        excludes=["refs/heads/topic/tw*"],
    )

    assert [record.refname for record in packed] == [
        "refs/heads/topic/one",
        "refs/tags/v1",
    ]
    records = read_packed_refs(repo.pygit_dir)
    assert set(records) == {"refs/heads/topic/one", "refs/tags/v1"}
    assert (repo.pygit_dir / "refs" / "heads" / "topic" / "two").is_file()
    assert (repo.pygit_dir / "refs" / "tags" / "release").is_file()


def test_all_ignores_include_narrowing_but_exclude_still_filters(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    repo.refs.set_branch("public", tip)
    repo.refs.set_branch("secret", tip)
    repo.refs.set_tag("v1", tip)

    packed = pack_refs(
        repo,
        all_refs=True,
        includes=["refs/tags/*"],
        excludes=["refs/heads/secret*"],
    )

    names = {record.refname for record in packed}
    assert names == {"refs/heads/main", "refs/heads/public", "refs/tags/v1"}
    assert "refs/heads/secret" not in read_packed_refs(repo.pygit_dir)
    assert (repo.pygit_dir / "refs" / "heads" / "secret").is_file()


def test_excluded_loose_shadow_preserves_old_packed_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    pack_refs(repo, all_refs=True)
    newer = _commit(repo, [tip], "newer", 3)
    repo.refs.set_branch("main", newer)

    packed = pack_refs(
        repo,
        all_refs=True,
        excludes=["refs/heads/main"],
    )

    assert packed == []
    records = read_packed_refs(repo.pygit_dir)
    assert records["refs/heads/main"].oid == tip
    loose = repo.pygit_dir / "refs" / "heads" / "main"
    assert loose.read_text(encoding="utf-8").strip() == newer
    assert repo.refs.get_branch("main") == newer


def test_star_pattern_matches_nested_ref_suffix(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    repo.refs.set_branch("topic/deep/leaf", tip)

    packed = pack_refs(repo, includes=["refs/heads/topic*"])

    assert [record.refname for record in packed] == ["refs/heads/topic/deep/leaf"]


def test_patterns_compose_with_no_prune(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    repo.refs.set_branch("feature", tip)

    pack_refs(
        repo,
        includes=["refs/heads/f*"],
        prune=False,
    )

    assert (repo.pygit_dir / "refs" / "heads" / "feature").is_file()
    assert read_packed_refs(repo.pygit_dir)["refs/heads/feature"].oid == tip


def test_installed_cli_repeated_patterns_and_help(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _, tip = _history(repo)
    repo.refs.set_branch("feature", tip)
    repo.refs.set_branch("forbidden", tip)
    repo.refs.set_tag("v1", tip)

    result = _run(
        repo,
        "pack-refs",
        "--include=refs/heads/f*",
        "--include=refs/tags/v*",
        "--exclude=refs/heads/forb*",
        "--no-prune",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    records = read_packed_refs(repo.pygit_dir)
    assert set(records) == {"refs/heads/feature", "refs/tags/v1"}
    assert (repo.pygit_dir / "refs" / "heads" / "feature").is_file()
    assert (repo.pygit_dir / "refs" / "heads" / "forbidden").is_file()
    assert (repo.pygit_dir / "refs" / "tags" / "v1").is_file()

    help_result = _run(repo, "pack-refs", "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "--include" in help_result.stdout
    assert "--exclude" in help_result.stdout
    assert "--all" in help_result.stdout
    assert "--no-prune" in help_result.stdout
