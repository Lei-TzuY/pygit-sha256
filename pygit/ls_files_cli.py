"""Modern CLI adapter for ``pygit ls-files``."""

from __future__ import annotations

import argparse
import fnmatch
import posixpath
import sys
from pathlib import Path
from typing import Sequence

from .index_plumbing import ls_files
from .ls_files_killed import killed_files
from .ls_files_others import other_files
from .repo import Repository


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def _cwd_prefix(repo: Repository) -> str:
    """Return the current worktree directory relative to the repository root."""
    root = Path(repo.worktree).resolve()
    current = Path.cwd().resolve()
    relative = current.relative_to(root).as_posix()
    return "" if relative == "." else relative


def _root_patterns(prefix: str, patterns: Sequence[str]) -> list[str]:
    """Translate cwd-relative CLI pathspecs into repository-root-relative paths."""
    if not patterns:
        return [prefix] if prefix else []

    translated: list[str] = []
    for pattern in patterns:
        normalized = posixpath.normpath(posixpath.join(prefix, pattern.replace("\\", "/")))
        if normalized == ".":
            normalized = ""
        if normalized == ".." or normalized.startswith("../"):
            raise ValueError(f"pathspec is outside the repository: {pattern!r}")
        translated.append(normalized)
    return translated


def _record_path(line: str) -> str:
    """Extract the repository-relative path from a formatted ls-files record."""
    _metadata, separator, path = line.rpartition("\t")
    return path if separator else line


def _pattern_matches_record(pattern: str, line: str) -> bool:
    """Return whether one root-relative pathspec matches an emitted record."""
    path = _record_path(line).rstrip("/")
    pattern = pattern.strip("/")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(path, pattern)
    return path == pattern or path.startswith(pattern + "/")


def _validate_error_unmatch(patterns: Sequence[str], lines: Sequence[str]) -> None:
    """Require every pathspec to match at least one record selected for output."""
    for pattern in patterns:
        if not any(_pattern_matches_record(pattern, line) for line in lines):
            raise KeyError(f"pathspec {pattern!r} did not match any selected file")


def _display_line(line: str, prefix: str, *, full_name: bool) -> str:
    """Render a root-relative ls-files record from the caller's directory."""
    if full_name or not prefix:
        return line

    metadata, separator, path = line.rpartition("\t")
    if not separator:
        metadata = ""
        path = line

    directory = path.endswith("/")
    bare_path = path[:-1] if directory else path
    relative = posixpath.relpath(bare_path, prefix)
    if directory:
        relative += "/"
    return f"{metadata}\t{relative}" if separator else relative


def run_ls_files(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit ls-files",
        description="Show information about files in the index and working tree.",
    )
    parser.add_argument("-c", "--cached", action="store_true", help="show cached paths")
    parser.add_argument("-s", "--stage", action="store_true", help="show mode, object, stage, and path")
    parser.add_argument("-u", "--unmerged", action="store_true", help="show only unmerged index stages")
    parser.add_argument("-d", "--deleted", action="store_true", help="show tracked paths deleted from the worktree")
    parser.add_argument("-m", "--modified", action="store_true", help="show tracked paths modified in the worktree")
    parser.add_argument("-o", "--others", action="store_true", help="show untracked worktree paths")
    parser.add_argument("-k", "--killed", action="store_true", help="show untracked paths obstructing tracked paths")
    parser.add_argument("-i", "--ignored", action="store_true", help="show only ignored untracked paths (requires --others --exclude-standard)")
    parser.add_argument("--exclude-standard", action="store_true", help="apply .gitignore, .pygitignore, and .pygit/info/exclude rules")
    parser.add_argument("--directory", action="store_true", help="show wholly-untracked directories with a trailing slash")
    parser.add_argument("--no-empty-directory", action="store_true", help="with --directory, suppress trees containing no files")
    parser.add_argument("--full-name", action="store_true", help="show paths relative to the repository root")
    parser.add_argument("--error-unmatch", action="store_true", help="fail if any supplied path pattern matches no selected file")
    parser.add_argument("-z", action="store_true", help="terminate records with NUL")
    parser.add_argument("path", nargs="*", metavar="PATH")
    args = parser.parse_args(list(argv))

    if args.ignored and not args.others:
        parser.error("--ignored currently requires --others")
    if args.ignored and not args.exclude_standard:
        parser.error("--ignored requires --exclude-standard")
    if args.exclude_standard and not args.others:
        parser.error("--exclude-standard currently applies to --others")
    if args.directory and not args.others:
        parser.error("--directory requires --others")
    if args.no_empty_directory and not args.directory:
        parser.error("--no-empty-directory requires --directory")

    repo = _find_repo()
    prefix = _cwd_prefix(repo)
    try:
        patterns = _root_patterns(prefix, args.path)
    except ValueError as exc:
        parser.error(str(exc))

    lines = []
    index_selector_requested = any((args.cached, args.stage, args.unmerged, args.deleted, args.modified))
    worktree_selector_requested = args.others or args.killed
    if index_selector_requested or not worktree_selector_requested:
        lines.extend(
            ls_files(
                repo,
                cached=args.cached,
                stage=args.stage,
                unmerged=args.unmerged,
                deleted=args.deleted,
                modified=args.modified,
                patterns=patterns,
                # Validate after every selector has contributed so mixed
                # index/worktree queries use the union of selected records.
                error_unmatch=False,
            )
        )
    if args.others:
        lines.extend(
            other_files(
                repo,
                ignored=args.ignored,
                exclude_standard=args.exclude_standard,
                patterns=patterns,
                directory=args.directory,
                no_empty_directory=args.no_empty_directory,
            )
        )
    if args.killed:
        lines.extend(killed_files(repo, patterns=patterns))

    lines = list(dict.fromkeys(lines))
    if args.error_unmatch and patterns:
        _validate_error_unmatch(patterns, lines)

    lines = [_display_line(line, prefix, full_name=args.full_name) for line in lines]
    if lines:
        separator = "\x00" if args.z else "\n"
        sys.stdout.write(separator.join(lines) + separator)
    return 0
