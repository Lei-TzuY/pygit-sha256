"""Rename detection helpers for status porcelain/short rendering.

Phase 159 keeps rename detection outside :mod:`pygit.repo` so the historical
``Repository.status()`` contract stays stable. Candidates are derived from the
HEAD tree and stage-zero index: a path deleted from HEAD is paired with a path
added to the index when their blob contents meet the configured similarity
threshold.

Pygit uses a deterministic byte-sequence similarity score. Exact object-id
matches are always R100; non-identical blobs use ``difflib.SequenceMatcher``.
This is intentionally compact and educational rather than a byte-for-byte
reimplementation of Git's diffcore-rename internals.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .objects import BlobObject
from .repo import Repository


@dataclass(frozen=True)
class RenameMatch:
    """One staged rename from a HEAD pathname to an index pathname."""

    source: str
    target: str
    score: int


def parse_similarity_threshold(value: Optional[str]) -> int:
    """Parse Git-style ``--find-renames=<n>`` similarity syntax.

    Common percentage spellings (``90%``) are accepted directly. Without a
    percent sign Git treats the token as a fraction with an implied decimal
    point before it: ``5`` -> 50%, ``05`` -> 5%, ``90`` -> 90%.
    ``None`` selects Git's normal 50% default.
    """
    if value is None or value == "":
        return 50
    token = value.strip()
    if not token:
        return 50
    try:
        if token.endswith("%"):
            score = float(token[:-1])
        elif "." in token:
            number = float(token)
            score = number * 100.0 if number <= 1.0 else number
        else:
            if not token.isdigit():
                raise ValueError
            score = int(token) * 100.0 / (10 ** len(token))
    except ValueError as exc:
        raise ValueError(f"invalid rename similarity threshold: {value!r}") from exc
    rounded = int(round(score))
    if not 0 <= rounded <= 100:
        raise ValueError(
            f"rename similarity threshold must be between 0% and 100%: {value!r}"
        )
    return rounded


def _blob_data(repo: Repository, oid: str) -> Optional[bytes]:
    try:
        obj = repo.store.read(oid)
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return None
    if not isinstance(obj, BlobObject):
        return None
    return obj.data


def _similarity_score(
    repo: Repository,
    source_oid: str,
    target_oid: str,
    cache: Dict[str, Optional[bytes]],
) -> int:
    if source_oid == target_oid:
        return 100
    if source_oid not in cache:
        cache[source_oid] = _blob_data(repo, source_oid)
    if target_oid not in cache:
        cache[target_oid] = _blob_data(repo, target_oid)
    before = cache[source_oid]
    after = cache[target_oid]
    if before is None or after is None:
        return 0
    if not before and not after:
        return 100
    ratio = difflib.SequenceMatcher(None, before, after, autojunk=False).ratio()
    return max(0, min(100, int(round(ratio * 100))))


def detect_staged_renames(
    repo: Repository,
    *,
    threshold: int = 50,
) -> List[RenameMatch]:
    """Detect staged delete/add pairs that represent renames.

    Detection is restricted to the HEAD->index side. An unstaged filesystem
    move therefore remains a worktree deletion plus an untracked destination
    until the destination is staged. Conflict-stage paths are excluded because
    stages 1/2/3 already have dedicated unmerged status semantics.
    """
    if not 0 <= threshold <= 100:
        raise ValueError("rename similarity threshold must be between 0 and 100")

    head_oid = repo.refs.resolve_head()
    if not head_oid:
        return []
    head_entries: Dict[str, Tuple[str, str]] = repo._commit_tree_entries(head_oid)
    index_entries = repo.index.entries
    conflict_paths = {entry.path for entry in repo.index.stage_entries()}

    deleted = [
        path
        for path in head_entries
        if path not in index_entries and path not in conflict_paths
    ]
    added = [
        path
        for path in index_entries
        if path not in head_entries and path not in conflict_paths
    ]
    if not deleted or not added:
        return []

    cache: Dict[str, Optional[bytes]] = {}
    candidates: List[Tuple[int, str, str]] = []
    for source in deleted:
        source_oid, _source_mode = head_entries[source]
        for target in added:
            target_entry = index_entries[target]
            score = _similarity_score(repo, source_oid, target_entry.sha, cache)
            if score >= threshold:
                candidates.append((score, source, target))

    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_sources = set()
    used_targets = set()
    matches: List[RenameMatch] = []
    for score, source, target in candidates:
        if source in used_sources or target in used_targets:
            continue
        used_sources.add(source)
        used_targets.add(target)
        matches.append(RenameMatch(source=source, target=target, score=score))

    return sorted(matches, key=lambda match: (match.target, match.source))
