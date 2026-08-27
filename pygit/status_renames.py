"""Rename/copy detection helpers for status rendering.

Phase 159 added staged HEAD-to-index rename matching. Phase 160 extends the
same compact similarity engine with Git-style copy candidates while keeping
``Repository.status()`` unchanged. Phase 161 adds ``status.renameLimit`` with
``diff.renameLimit`` fallback and keeps exact object-id matches outside the
expensive exhaustive similarity fallback, matching Git's user-visible limit
semantics.

The important copy compatibility boundary is source eligibility: ordinary Git
status copy detection considers a source only when that path itself changed in
the same HEAD-to-index changeset. Unmodified source files are intentionally not
searched; native ``git status`` does not expose diff's ``--find-copies-harder``
option.

Pygit uses deterministic byte-sequence similarity. Exact object-id matches are
100; non-identical blobs use ``difflib.SequenceMatcher``. This keeps the public
threshold/limit contracts useful without claiming byte-for-byte diffcore
implementation equivalence.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .objects import BlobObject
from .repo import Repository


_DEFAULT_RENAME_LIMIT = 1000


@dataclass(frozen=True)
class RenameMatch:
    """One staged rename from a HEAD pathname to an index pathname."""

    source: str
    target: str
    score: int


@dataclass(frozen=True)
class CopyMatch:
    """One staged copy from a changed HEAD pathname to an added index path."""

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


def parse_rename_limit(value: str, *, key: str = "renameLimit") -> int:
    """Parse Git-style integer config used by rename-limit settings.

    Git's config integer parser accepts ``k``, ``m``, and ``g`` suffixes using
    powers of 1024. Zero and negative values behave as unlimited for rename
    detection; positive values cap the exhaustive fallback candidate counts.
    """
    token = value.strip().lower()
    if not token:
        raise ValueError(f"invalid {key} value: {value!r}")

    multiplier = 1
    if token[-1:] in {"k", "m", "g"}:
        suffix = token[-1]
        token = token[:-1]
        multiplier = {
            "k": 1024,
            "m": 1024 * 1024,
            "g": 1024 * 1024 * 1024,
        }[suffix]
    try:
        number = int(token, 10)
    except ValueError as exc:
        raise ValueError(f"invalid {key} value: {value!r}") from exc
    return number * multiplier


def configured_rename_limit(repo: Repository) -> int:
    """Resolve ``status.renameLimit`` -> ``diff.renameLimit`` -> Git default.

    Current Git documents ``status.renameLimit`` as taking precedence and
    falling back to ``diff.renameLimit``. The diff machinery's current default
    is 1000 candidates when neither value is configured.
    """
    status_value = repo.config_get("status", "renameLimit")
    if status_value is not None:
        return parse_rename_limit(status_value, key="status.renameLimit")
    diff_value = repo.config_get("diff", "renameLimit")
    if diff_value is not None:
        return parse_rename_limit(diff_value, key="diff.renameLimit")
    return _DEFAULT_RENAME_LIMIT


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


def _status_trees(
    repo: Repository,
) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, object], Set[str]]:
    head_oid = repo.refs.resolve_head()
    if not head_oid:
        return {}, {}, set()
    head_entries: Dict[str, Tuple[str, str]] = repo._commit_tree_entries(head_oid)
    index_entries = repo.index.entries
    conflict_paths = {entry.path for entry in repo.index.stage_entries()}
    return head_entries, index_entries, conflict_paths


def _exhaustive_allowed(source_count: int, target_count: int, limit: int) -> bool:
    """Return whether the expensive similarity fallback may run.

    Git's rename limit gates the exhaustive portion only. A non-positive limit
    is treated as unlimited. Positive limits allow the fallback when both the
    remaining source and destination populations fit within the configured
    bound.
    """
    if limit <= 0:
        return True
    return source_count <= limit and target_count <= limit


def detect_staged_renames(
    repo: Repository,
    *,
    threshold: int = 50,
    limit: Optional[int] = None,
) -> List[RenameMatch]:
    """Detect staged delete/add pairs that represent renames.

    Detection is restricted to the HEAD->index side. Exact object-id matches
    are paired first and are not blocked by ``renameLimit``. The configured
    limit applies only to the remaining O(N^2) similarity fallback. Conflict
    stages remain excluded from ordinary rename status classification.
    """
    if not 0 <= threshold <= 100:
        raise ValueError("rename similarity threshold must be between 0 and 100")

    effective_limit = configured_rename_limit(repo) if limit is None else limit
    head_entries, index_entries, conflict_paths = _status_trees(repo)
    if not head_entries:
        return []

    deleted = sorted(
        path
        for path in head_entries
        if path not in index_entries and path not in conflict_paths
    )
    added = sorted(
        path
        for path in index_entries
        if path not in head_entries and path not in conflict_paths
    )
    if not deleted or not added:
        return []

    used_sources: Set[str] = set()
    used_targets: Set[str] = set()
    matches: List[RenameMatch] = []

    # Cheap exact-object pass. Git performs inexpensive pairing before the
    # rename-limit-controlled exhaustive similarity search.
    exact_pairs: List[Tuple[str, str]] = []
    for source in deleted:
        source_oid, _source_mode = head_entries[source]
        for target in added:
            if source_oid == index_entries[target].sha:
                exact_pairs.append((source, target))
    for source, target in sorted(exact_pairs):
        if source in used_sources or target in used_targets:
            continue
        used_sources.add(source)
        used_targets.add(target)
        matches.append(RenameMatch(source=source, target=target, score=100))

    remaining_sources = [path for path in deleted if path not in used_sources]
    remaining_targets = [path for path in added if path not in used_targets]
    if (
        remaining_sources
        and remaining_targets
        and _exhaustive_allowed(
            len(remaining_sources),
            len(remaining_targets),
            effective_limit,
        )
    ):
        cache: Dict[str, Optional[bytes]] = {}
        candidates: List[Tuple[int, str, str]] = []
        for source in remaining_sources:
            source_oid, _source_mode = head_entries[source]
            for target in remaining_targets:
                target_entry = index_entries[target]
                score = _similarity_score(repo, source_oid, target_entry.sha, cache)
                if score >= threshold:
                    candidates.append((score, source, target))

        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        for score, source, target in candidates:
            if source in used_sources or target in used_targets:
                continue
            used_sources.add(source)
            used_targets.add(target)
            matches.append(RenameMatch(source=source, target=target, score=score))

    return sorted(matches, key=lambda match: (match.target, match.source))


def detect_staged_copies(
    repo: Repository,
    *,
    threshold: int = 50,
    exclude_targets: Optional[Set[str]] = None,
    limit: Optional[int] = None,
) -> List[CopyMatch]:
    """Detect copies from changed HEAD paths to newly-added index paths.

    Only paths changed in the same HEAD->index comparison are eligible copy
    sources. A source may feed multiple targets. Exact preimage-object matches
    are classified first even when the rename limit is low; the configured
    limit only gates the expensive similarity fallback for unmatched targets.
    """
    if not 0 <= threshold <= 100:
        raise ValueError("copy similarity threshold must be between 0 and 100")

    effective_limit = configured_rename_limit(repo) if limit is None else limit
    head_entries, index_entries, conflict_paths = _status_trees(repo)
    if not head_entries:
        return []

    excluded = exclude_targets or set()
    added = sorted(
        path
        for path in index_entries
        if path not in head_entries
        and path not in conflict_paths
        and path not in excluded
    )
    changed_sources: List[str] = []
    for path, (head_oid, head_mode) in head_entries.items():
        if path in conflict_paths:
            continue
        index_entry = index_entries.get(path)
        if index_entry is None:
            continue
        if index_entry.sha != head_oid or index_entry.mode != head_mode:
            changed_sources.append(path)
    changed_sources.sort()

    if not added or not changed_sources:
        return []

    matches: List[CopyMatch] = []
    unmatched_targets: List[str] = []

    # Copy sources are reusable, so each target independently chooses the
    # lexicographically first exact preimage source when one exists.
    for target in added:
        target_entry = index_entries[target]
        exact_sources = [
            source
            for source in changed_sources
            if head_entries[source][0] == target_entry.sha
        ]
        if exact_sources:
            matches.append(
                CopyMatch(source=exact_sources[0], target=target, score=100)
            )
        else:
            unmatched_targets.append(target)

    if (
        unmatched_targets
        and _exhaustive_allowed(
            len(changed_sources),
            len(unmatched_targets),
            effective_limit,
        )
    ):
        cache: Dict[str, Optional[bytes]] = {}
        for target in unmatched_targets:
            target_entry = index_entries[target]
            candidates: List[Tuple[int, str]] = []
            for source in changed_sources:
                source_oid, _source_mode = head_entries[source]
                score = _similarity_score(repo, source_oid, target_entry.sha, cache)
                if score >= threshold:
                    candidates.append((score, source))
            if not candidates:
                continue
            candidates.sort(key=lambda item: (-item[0], item[1]))
            score, source = candidates[0]
            matches.append(CopyMatch(source=source, target=target, score=score))

    return sorted(matches, key=lambda match: (match.target, match.source))
