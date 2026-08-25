"""Phase 79 tests: strict local ``show-ref`` plumbing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import Repository, format_show_refs, pack_refs, show_refs
from pygit.objects import CommitObject, Identity, TagObject, TreeObject


IDENT = Identity("Tester", "tester@example.com", 1, "+0000")


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _commit(repo: Repository, tree: str, *, parents=(), message: str = "commit") -> str:
    return repo.store.write(
        CommitObject(
            tree=tree,
            parents=list(parents),
            author=IDENT,
            committer=IDENT,
            message=message,
        )
    )


def _fixture(repo: Repository) -> dict[str, str]:
    tree = repo.store.write(TreeObject([]))
    first = _commit(repo, tree, message="first")
    second = _commit(repo, tree, parents=(first,), message="second")
    annotated = repo.store.write(
        TagObject(
            target_sha=first,
            target_type=b"commit",
            tag_name="ann",
            tagger=IDENT,
            message="annotated",
        )
    )

    repo.refs.set_branch("main", first)
    repo.refs.set_branch("topic", first)
    repo.refs.set_remote("origin", "main", second)
    repo.refs.set_tag("ann", annotated)
    repo.refs.set_tag("light", second)
    repo.refs.set_head_symbolic("main")

    # Exercise packed-only refs, then recreate topic as a loose shadow whose
    # value differs from the packed record.
    pack_refs(repo, all_refs=True, prune=True)
    repo.refs.set_branch("topic", second)

    missing = "f" * 64
    broken_tag = repo.store.write(
        TagObject(
            target_sha=missing,
            target_type=b"commit",
            tag_name="broken",
            tagger=IDENT,
            message="broken target",
        )
    )
    repo.refs.set_tag("broken", broken_tag)
    return {
        "tree": tree,
        "first": first,
        "second": second,
        "annotated": annotated,
        "broken_tag": broken_tag,
        "missing": missing,
    }


def _cli(repo: Repository, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", *args],
        cwd=repo.worktree,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_lists_loose_and_packed_refs_with_loose_shadowing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = show_refs(repo)
    assert [entry.refname for entry in entries] == [
        "refs/heads/main",
        "refs/heads/topic",
        "refs/remotes/origin/main",
        "refs/tags/ann",
        "refs/tags/broken",
        "refs/tags/light",
    ]
    values = {entry.refname: entry.oid for entry in entries}
    assert values["refs/heads/main"] == ids["first"]
    assert values["refs/heads/topic"] == ids["second"]
    assert values["refs/remotes/origin/main"] == ids["second"]
    assert values["refs/tags/ann"] == ids["annotated"]


def test_head_namespace_filters_and_tail_patterns_compose(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    branches = show_refs(repo, include_head=True, branches=True)
    assert [(entry.refname, entry.oid) for entry in branches] == [
        ("HEAD", ids["first"]),
        ("refs/heads/main", ids["first"]),
        ("refs/heads/topic", ids["second"]),
    ]
    assert [entry.refname for entry in show_refs(repo, patterns=("main",))] == [
        "refs/heads/main",
        "refs/remotes/origin/main",
    ]
    assert [entry.refname for entry in show_refs(repo, branches=True, tags=True)] == [
        "refs/heads/main",
        "refs/heads/topic",
        "refs/tags/ann",
        "refs/tags/broken",
        "refs/tags/light",
    ]


def test_verify_requires_exact_existing_fully_qualified_refs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = show_refs(
        repo,
        verify_refs=("refs/heads/main", "refs/tags/ann"),
    )
    assert [(entry.refname, entry.oid) for entry in entries] == [
        ("refs/heads/main", ids["first"]),
        ("refs/tags/ann", ids["annotated"]),
    ]
    with pytest.raises(ValueError, match="exact ref"):
        show_refs(repo, verify_refs=("main",))
    with pytest.raises(KeyError):
        show_refs(repo, verify_refs=("refs/heads/missing",))
    with pytest.raises(ValueError, match="filters"):
        show_refs(repo, verify_refs=("refs/heads/main",), tags=True)


def test_dereference_adds_only_annotated_tag_peeled_records(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = show_refs(
        repo,
        tags=True,
        patterns=("ann", "light"),
        dereference=True,
    )
    assert [(entry.refname, entry.oid, entry.dereferenced) for entry in entries] == [
        ("refs/tags/ann", ids["annotated"], False),
        ("refs/tags/ann^{}", ids["first"], True),
        ("refs/tags/light", ids["second"], False),
    ]


def test_broken_annotated_tag_is_visible_until_dereference_is_requested(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = show_refs(repo, tags=True, patterns=("broken",))
    assert [(entry.refname, entry.oid) for entry in entries] == [
        ("refs/tags/broken", ids["broken_tag"]),
    ]
    with pytest.raises(KeyError):
        show_refs(repo, tags=True, patterns=("broken",), dereference=True)


def test_format_hash_abbrev_and_missing_target_prefixes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)
    entries = show_refs(repo, tags=True, patterns=("ann",), dereference=True)
    full = format_show_refs(repo, entries).decode("utf-8")
    assert full == (
        f"{ids['annotated']} refs/tags/ann\n"
        f"{ids['first']} refs/tags/ann^{{}}\n"
    )
    assert format_show_refs(repo, entries, hash_only=True).decode().splitlines() == [
        ids["annotated"],
        ids["first"],
    ]
    assert format_show_refs(repo, entries, hash_length=8).decode().splitlines() == [
        ids["annotated"][:8],
        ids["first"][:8],
    ]

    abbreviated = format_show_refs(repo, entries, abbrev=8).decode().splitlines()
    for line, oid in zip(abbreviated, (ids["annotated"], ids["first"])):
        prefix, refname = line.split(" ", 1)
        assert len(prefix) >= 8
        assert oid.startswith(prefix)
        assert refname.startswith("refs/tags/ann")

    combined = format_show_refs(repo, entries, hash_length=7, abbrev=10).decode().splitlines()
    assert all(" " not in line and len(line) >= 10 for line in combined)

    repo.refs.set_branch("ghost", "e" * 64)
    ghost = show_refs(repo, branches=True, patterns=("ghost",))
    assert format_show_refs(repo, ghost, abbrev=8) == b"eeeeeeee refs/heads/ghost\n"

    with pytest.raises(ValueError, match="hash length"):
        format_show_refs(repo, entries, hash_length=65)
    with pytest.raises(ValueError, match="abbreviation length"):
        format_show_refs(repo, entries, abbrev=0)


def test_malformed_packed_or_loose_refs_fail_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    packed = repo.pygit_dir / "packed-refs"
    packed.write_text("not-an-oid refs/heads/main\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="packed-refs"):
        show_refs(repo)

    repo2 = Repository.init(str(tmp_path / "repo2"))
    tree = repo2.store.write(TreeObject([]))
    commit = _commit(repo2, tree)
    repo2.refs.set_branch("main", commit)
    bad = repo2.pygit_dir / "refs" / "heads" / "bad"
    bad.write_text("not-an-oid\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Malformed ref"):
        show_refs(repo2)


def test_show_ref_is_read_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _fixture(repo)
    packed = repo.pygit_dir / "packed-refs"
    before_packed = packed.read_bytes()
    before_refs = {
        path.relative_to(repo.pygit_dir).as_posix(): path.read_bytes()
        for path in (repo.pygit_dir / "refs").rglob("*")
        if path.is_file()
    }
    show_refs(repo, include_head=True)
    show_refs(repo, tags=True, patterns=("ann",), dereference=True)
    after_refs = {
        path.relative_to(repo.pygit_dir).as_posix(): path.read_bytes()
        for path in (repo.pygit_dir / "refs").rglob("*")
        if path.is_file()
    }
    assert packed.read_bytes() == before_packed
    assert after_refs == before_refs


def test_installed_cli_routes_show_ref_and_preserves_exit_semantics(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    ids = _fixture(repo)

    branches = _cli(repo, "show-ref", "--head", "--branches")
    assert branches.returncode == 0, branches.stderr
    assert branches.stdout == (
        f"{ids['first']} HEAD\n"
        f"{ids['first']} refs/heads/main\n"
        f"{ids['second']} refs/heads/topic\n"
    )

    deref = _cli(repo, "show-ref", "--tags", "--dereference", "ann")
    assert deref.returncode == 0, deref.stderr
    assert deref.stdout == (
        f"{ids['annotated']} refs/tags/ann\n"
        f"{ids['first']} refs/tags/ann^{{}}\n"
    )

    hashed = _cli(repo, "show-ref", "--hash=8", "--tags", "ann")
    assert hashed.returncode == 0, hashed.stderr
    assert hashed.stdout == ids["annotated"][:8] + "\n"

    verified = _cli(repo, "show-ref", "--verify", "refs/heads/main")
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout == f"{ids['first']} refs/heads/main\n"

    quiet_missing = _cli(repo, "show-ref", "--verify", "--quiet", "refs/heads/missing")
    assert quiet_missing.returncode == 1
    assert quiet_missing.stdout == ""
    assert quiet_missing.stderr == ""

    no_match = _cli(repo, "show-ref", "definitely-missing")
    assert no_match.returncode == 1
    assert no_match.stdout == ""
    assert no_match.stderr == ""

    invalid = _cli(repo, "show-ref", "--verify", "main")
    assert invalid.returncode == 1
    assert invalid.stdout == ""
    assert "exact ref name" in invalid.stderr
