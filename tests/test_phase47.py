"""
Phase 47 tests: ref querying, formatting, graph filters, and refname validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pygit import Repository
from pygit.entrypoint import dispatch
from pygit.objects import TagObject
from pygit.ref_query import check_ref_format, format_ref, query_refs


def _commit_file(repo: Repository, content: str, message: str) -> str:
    path = repo.worktree / "f.txt"
    path.write_text(content, encoding="utf-8")
    repo.add(["f.txt"])
    return repo.commit(
        message,
        author_name="Tester",
        author_email="tester@example.com",
    )


def _fixture_repo(tmp_path: Path) -> tuple[Repository, str, str]:
    repo = Repository.init(str(tmp_path / "r"))
    first = _commit_file(repo, "one\n", "first subject\n\nbody")
    repo.refs.set_branch("release", first)

    second = _commit_file(repo, "two\n", "second subject")
    repo.refs.set_remote("origin", "main", second)

    tag = TagObject(
        target_sha=second,
        target_type=b"commit",
        tag_name="v2",
        message="release v2\n\nnotes",
    )
    tag_sha = repo.store.write(tag)
    repo.refs.set_tag("v2", tag_sha)
    return repo, first, second


class TestRefQuery:
    def test_prefix_glob_sort_and_count(self, tmp_path: Path) -> None:
        repo, _, _ = _fixture_repo(tmp_path)

        heads = query_refs(repo, patterns=["refs/heads/"], sort_keys=["-refname"])
        assert [record.refname for record in heads] == [
            "refs/heads/release",
            "refs/heads/main",
        ]

        remote = query_refs(repo, patterns=["refs/remotes/*"])
        assert [record.refname for record in remote] == ["refs/remotes/origin/main"]

        limited = query_refs(repo, sort_keys=["refname"], count=2)
        assert len(limited) == 2
        assert [record.refname for record in limited] == sorted(
            record.refname for record in limited
        )

    def test_format_atoms_cover_commit_and_tag_metadata(self, tmp_path: Path) -> None:
        repo, _, second = _fixture_repo(tmp_path)
        records = query_refs(repo, patterns=["refs/heads/main", "refs/tags/v2"])
        by_name = {record.refname: record for record in records}

        main = format_ref(
            by_name["refs/heads/main"],
            "%(refname:short)|%(objecttype)|%(objectname:short=8)|"
            "%(subject)|%(authorname)|%(authoremail)",
        )
        assert main == (
            f"main|commit|{second[:8]}|second subject|"
            "Tester|<tester@example.com>"
        )

        tag = format_ref(
            by_name["refs/tags/v2"],
            "%(refname:short)|%(objecttype)|%(contents:subject)|%(taggername)",
        )
        assert tag == "v2|tag|release v2|Unknown"

    def test_hex_escapes_and_literal_percent(self, tmp_path: Path) -> None:
        repo, _, _ = _fixture_repo(tmp_path)
        record = query_refs(repo, patterns=["refs/heads/main"])[0]
        assert format_ref(record, "%(refname:short)%09%%") == "main\t%"

    def test_contains_and_merged_filters_peel_tags(self, tmp_path: Path) -> None:
        repo, first, second = _fixture_repo(tmp_path)

        contains_second = {
            record.refname for record in query_refs(repo, contains=second)
        }
        assert "refs/heads/main" in contains_second
        assert "refs/tags/v2" in contains_second
        assert "refs/heads/release" not in contains_second

        no_contains_second = {
            record.refname for record in query_refs(repo, no_contains=second)
        }
        assert "refs/heads/release" in no_contains_second
        assert "refs/heads/main" not in no_contains_second

        merged_first = {
            record.refname for record in query_refs(repo, merged=first)
        }
        assert merged_first == {"refs/heads/release"}

        not_merged_first = {
            record.refname for record in query_refs(repo, no_merged=first)
        }
        assert "refs/heads/main" in not_merged_first
        assert "refs/tags/v2" in not_merged_first

    def test_bad_sort_or_format_field_is_rejected(self, tmp_path: Path) -> None:
        repo, _, _ = _fixture_repo(tmp_path)
        with pytest.raises(ValueError, match="Unsupported sort field"):
            query_refs(repo, sort_keys=["nonesuch"])

        record = query_refs(repo)[0]
        with pytest.raises(ValueError, match="Unsupported format atom"):
            format_ref(record, "%(nonesuch)")


class TestRefFormat:
    @pytest.mark.parametrize(
        "name",
        [
            "refs/heads/foo..bar",
            "refs/heads/.hidden",
            "refs/heads/topic.lock",
            "refs/heads/has space",
            "refs/heads/a@{b",
            "refs/heads/trailing.",
            "refs//heads/main",
            "@",
        ],
    )
    def test_invalid_refnames(self, name: str) -> None:
        with pytest.raises(ValueError):
            check_ref_format(name)

    def test_onelevel_branch_and_normalize_modes(self) -> None:
        assert check_ref_format("refs/heads/main") == "refs/heads/main"
        assert check_ref_format("main", branch=True) == "main"

        with pytest.raises(ValueError):
            check_ref_format("main")
        with pytest.raises(ValueError):
            check_ref_format("-danger", branch=True)

        assert (
            check_ref_format("//refs//heads/main", normalize=True)
            == "refs/heads/main"
        )


class TestPhase47CLI:
    def test_for_each_ref_dispatch(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo, _, _ = _fixture_repo(tmp_path)
        monkeypatch.chdir(repo.worktree)
        capsys.readouterr()  # discard Repository.init() status output

        code = dispatch(
            [
                "for-each-ref",
                "--sort=refname",
                "--format=%(refname:short)|%(objecttype)",
                "refs/heads/",
            ]
        )
        assert code == 0
        assert capsys.readouterr().out.splitlines() == [
            "main|commit",
            "release|commit",
        ]

    def test_check_ref_format_dispatch(self, capsys) -> None:
        code = dispatch(
            ["check-ref-format", "--normalize", "//refs//heads/topic"]
        )
        assert code == 0
        assert capsys.readouterr().out.strip() == "refs/heads/topic"

        code = dispatch(["check-ref-format", "refs/heads/bad..name"])
        assert code == 1
        assert "error:" in capsys.readouterr().err
