"""
Minimal ``.pygitignore`` support.

The matcher intentionally implements the useful subset of gitignore syntax:
blank lines, comments, negation, directory patterns, rooted patterns, and
shell-style wildcards. Patterns are evaluated in order so later entries can
override earlier ones. Phase 155 additionally exposes whether the path itself,
rather than one of its ignored ancestors, is the final matching ignore target.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List


@dataclass(frozen=True)
class IgnorePattern:
    pattern: str
    negated: bool = False
    directory_only: bool = False
    rooted: bool = False

    def matches(self, path: str, is_dir: bool = False) -> bool:
        candidate = path.strip("/")
        if not candidate:
            return False

        if self.directory_only:
            parts = candidate.split("/")
            prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            return any(self._matches_one(prefix) for prefix in prefixes)

        return self._matches_one(candidate)

    def matches_explicitly(self, path: str, is_dir: bool = False) -> bool:
        """Return whether this pattern directly matches ``path`` itself.

        Ordinary ignore evaluation lets a directory-only pattern match all of
        that directory's descendants. ``status --ignored=matching`` needs to
        distinguish that inherited ignored state from the directory path that
        actually matched the pattern, so directory-only patterns are direct
        matches only for directories themselves.
        """
        candidate = path.strip("/")
        if not candidate:
            return False
        if self.directory_only and not is_dir:
            return False
        return self._matches_one(candidate)

    def _matches_one(self, candidate: str) -> bool:
        pattern = self.pattern.strip("/")
        if not pattern:
            return False

        if self.rooted:
            return fnmatch.fnmatchcase(candidate, pattern)

        if "/" not in pattern:
            return any(
                fnmatch.fnmatchcase(part, pattern)
                for part in PurePosixPath(candidate).parts
            )

        return (
            fnmatch.fnmatchcase(candidate, pattern)
            or fnmatch.fnmatchcase(candidate, f"*/{pattern}")
        )


class IgnoreMatcher:
    """Load and evaluate patterns from a worktree's ignore files."""

    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        self.patterns = (
            self._load(worktree / ".pygitignore")
            + self._load(worktree / ".gitignore")
            + self._load(worktree / ".pygit" / "info" / "exclude")
        )

    @staticmethod
    def _load(path: Path) -> List[IgnorePattern]:
        if not path.exists():
            return []

        patterns: List[IgnorePattern] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            negated = line.startswith("!")
            if negated:
                line = line[1:]
            directory_only = line.endswith("/")
            rooted = line.startswith("/")
            line = line.strip("/")
            if line:
                patterns.append(
                    IgnorePattern(
                        pattern=line,
                        negated=negated,
                        directory_only=directory_only,
                        rooted=rooted,
                    )
                )
        return patterns

    def is_ignored(self, path: str, is_dir: bool = False) -> bool:
        ignored = False
        for pattern in self.patterns:
            if pattern.matches(path, is_dir=is_dir):
                ignored = not pattern.negated
        return ignored

    def is_explicitly_ignored(self, path: str, is_dir: bool = False) -> bool:
        """Return final ignored state from patterns directly matching ``path``.

        Inherited directory matches are deliberately excluded. Later negated
        patterns retain their normal precedence, which is important when
        deciding whether a directory should be the single matching-mode record
        or whether its ignored descendants should be emitted individually.
        """
        matched = False
        ignored = False
        for pattern in self.patterns:
            if pattern.matches_explicitly(path, is_dir=is_dir):
                matched = True
                ignored = not pattern.negated
        return matched and ignored
