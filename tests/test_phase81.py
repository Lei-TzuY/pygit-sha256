"""Phase 81 tests: stdin filtering with ``show-ref --exclude-existing``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pygit import ExcludeExistingResult, Repository, exclude_existing_refs
from pygit.packed_refs import PackedRef, write_packed_refs


FAKE_OID = "a" * 64
OTHER_OID = "b" * 64


def _repo(tmp_path: Path) -> Repository:
    return Repository.init(str(tmp_path / "repo"))


def _run(
    repo: Repository,
    *args: str,
    input_bytes: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "pygit", "show-ref", *args],
        cwd=repo.worktree,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _write_loose(repo: Repository, refname: str, value: str) -> Path:
    path = repo.pygit_dir / refname
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return path


def test_api_filters_storage_records_without_resolving_objects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_loose(repo, "refs/heads/alias", "ref: refs/heads/missing")
    write_packed_refs(repo.pygit_dir, [PackedRef(FAKE_OID, "refs/tags/packed")])

    result = exclude_existing_refs(
        repo,
        [
            b"one refs/heads/alias\n",
            b"two refs/tags/packed^{}\n",
            b"three refs/heads/new^{}\n",
        ],
    )

    assert isinstance(result, ExcludeExistingResult)
    assert result.output == b"three refs/heads/new\n"
    assert result.warnings == ()
    assert not repo.store.exists(FAKE_OID)


def test_pattern_head_match_precedes_ref_validation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = exclude_existing_refs(
        repo,
        [
            b"refs/tags/not-considered..bad\n",
            b"refs/heads/bad..name\n",
            b"refs/heads/new\n",
        ],
        pattern="refs/heads/",
    )

    assert result.output == b"refs/heads/new\n"
    assert len(result.warnings) == 1
    assert "bad..name" in result.warnings[0]
    assert "not-considered" not in result.warnings[0]


def test_filter_preserves_prefix_bytes_and_line_endings_while_stripping_deref(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = exclude_existing_refs(
        repo,
        [b"\xff arbitrary prefix refs/heads/new^{}\r\n", b"refs/heads/second\n"],
    )

    assert result.output == b"\xff arbitrary prefix refs/heads/new\r\nrefs/heads/second\n"
    assert result.warnings == ()


def test_malformed_input_warns_and_skips_without_failing_filter(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = exclude_existing_refs(
        repo,
        [b"\n", b"refs/heads/trailing-space \n", b"refs/heads/\xff\n"],
    )

    assert result.output == b""
    assert len(result.warnings) == 3
    assert any("malformed ref line" in warning for warning in result.warnings)
    assert any("invalid refname" in warning for warning in result.warnings)
    assert any("non-UTF-8" in warning for warning in result.warnings)


def test_cli_filters_stdin_and_optional_prefix_requires_equals_form(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _write_loose(repo, "refs/heads/existing", OTHER_OID)

    filtered = _run(
        repo,
        "--exclude-existing=refs/heads/",
        input_bytes=(
            b"x refs/tags/new\n"
            b"y refs/heads/existing\n"
            b"z refs/heads/new^{}\n"
        ),
    )
    assert filtered.returncode == 0, filtered.stderr.decode()
    assert filtered.stdout == b"z refs/heads/new\n"
    assert filtered.stderr == b""

    spaced_value = _run(
        repo,
        "--exclude-existing",
        "refs/heads/",
        input_bytes=b"refs/heads/new\n",
    )
    assert spaced_value.returncode == 2
    assert b"takes no positional refs" in spaced_value.stderr


def test_cli_warns_for_invalid_input_but_returns_success(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = _run(
        repo,
        "--exclude-existing",
        input_bytes=b"refs/heads/bad..name\nrefs/heads/good\n",
    )

    assert result.returncode == 0
    assert result.stdout == b"refs/heads/good\n"
    assert b"warning:" in result.stderr
    assert b"bad..name" in result.stderr


def test_cli_rejects_listing_and_formatting_options_in_filter_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    for option in ("--tags", "--head", "--dereference", "--hash", "--quiet"):
        result = _run(repo, "--exclude-existing", option)
        assert result.returncode == 2
        assert b"cannot be combined" in result.stderr


def test_corrupt_local_ref_storage_fails_loudly_before_filtering(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo.pygit_dir / "packed-refs").write_text(
        "not-an-oid refs/heads/bad\n",
        encoding="utf-8",
    )

    result = _run(repo, "--exclude-existing", input_bytes=b"refs/heads/new\n")
    assert result.returncode == 1
    assert result.stdout == b""
    assert b"error:" in result.stderr
    assert b"packed-refs" in result.stderr


def test_symlinked_local_ref_storage_is_not_followed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside-ref"
    outside.write_text(FAKE_OID + "\n", encoding="utf-8")
    link = repo.pygit_dir / "refs" / "heads" / "linked"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(RuntimeError, match="symbolic-link ref storage"):
        exclude_existing_refs(repo, [b"refs/heads/new\n"])
