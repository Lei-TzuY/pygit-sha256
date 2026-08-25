"""Strict, read-only reflog inspection built on Phase 72 parsing rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .reflog_expire import _read_records, _safe_log_path, _target_logs
from .repo import Repository


@dataclass(frozen=True)
class ReflogShowEntry:
    """One displayed reflog record.

    ``index`` is always the selector index counted from the newest record of
    that ref, even when the final output is reversed.
    """

    ref: str
    index: int
    old_oid: str
    new_oid: str
    timestamp: int
    message: str

    @property
    def selector(self) -> str:
        return f"{self.ref}@{{{self.index}}}"


def _log_exists(repo: Repository, ref: str) -> bool:
    try:
        return _safe_log_path(repo, ref).is_file()
    except (ValueError, RuntimeError):
        return False


def normalize_reflog_ref(repo: Repository, ref: str) -> str:
    """Resolve convenient reflog names without resolving object revisions.

    ``HEAD`` and fully-qualified ``refs/...`` names are accepted directly.
    Short names are matched only against existing reflog files, so a branch
    named ``topic`` may be shown as ``topic`` without inventing a ref when the
    log does not exist.  Ambiguous short names fail loudly.
    """

    if not ref:
        raise ValueError("reflog ref name must not be empty")
    if ref == "HEAD" or ref.startswith("refs/"):
        return ref

    candidates = [f"refs/heads/{ref}", f"refs/remotes/{ref}"]
    if ref == "stash":
        candidates.append("refs/stash")
    matches = [candidate for candidate in candidates if _log_exists(repo, candidate)]
    if len(matches) > 1:
        raise ValueError(f"ambiguous reflog ref name: {ref!r}")
    if matches:
        return matches[0]

    # Preserve the historical read-only behaviour for a missing short name:
    # it simply has no entries rather than being interpreted as an object rev.
    return ref


def _records_for_ref(
    repo: Repository,
    canonical_ref: str,
    *,
    display_ref: Optional[str] = None,
    allow_missing: bool,
) -> List[ReflogShowEntry]:
    try:
        path = _safe_log_path(repo, canonical_ref)
    except ValueError:
        if allow_missing and not canonical_ref.startswith("refs/"):
            return []
        raise
    if not path.exists():
        if allow_missing:
            return []
        raise FileNotFoundError(f"reflog does not exist: {canonical_ref}")
    if not path.is_file():
        raise RuntimeError(f"reflog is not a regular file: {canonical_ref}")

    records = _read_records(canonical_ref, path)
    shown_ref = display_ref or canonical_ref
    newest_first = list(reversed(records))
    return [
        ReflogShowEntry(
            ref=shown_ref,
            index=index,
            old_oid=record.old_oid,
            new_oid=record.new_oid,
            timestamp=record.timestamp,
            message=record.message,
        )
        for index, record in enumerate(newest_first)
    ]


def show_reflog(
    repo: Repository,
    ref: str = "HEAD",
    *,
    all_refs: bool = False,
    max_count: int = 0,
    reverse: bool = False,
) -> Tuple[ReflogShowEntry, ...]:
    """Return strict reflog records without modifying repository state.

    Default output order is newest-first.  ``--all`` semantics globally order
    records by timestamp while each selector index remains local to its ref.
    Missing explicitly requested logs preserve the legacy empty-result
    behaviour; malformed existing logs always fail loudly.
    """

    if max_count < 0:
        raise ValueError("--max-count must be non-negative")
    if all_refs and ref != "HEAD":
        raise ValueError("an explicit reflog ref cannot be combined with --all")

    entries: List[ReflogShowEntry] = []
    if all_refs:
        for canonical_ref, _ in _target_logs(repo, (), all_refs=True):
            entries.extend(
                _records_for_ref(
                    repo,
                    canonical_ref,
                    display_ref=canonical_ref,
                    allow_missing=False,
                )
            )
        entries.sort(key=lambda entry: (-entry.timestamp, entry.ref, entry.index))
    else:
        canonical_ref = normalize_reflog_ref(repo, ref)
        display_ref = ref if canonical_ref != ref else canonical_ref
        entries = _records_for_ref(
            repo,
            canonical_ref,
            display_ref=display_ref,
            allow_missing=True,
        )

    if reverse:
        entries.reverse()
    if max_count:
        entries = entries[:max_count]
    return tuple(entries)


def format_reflog_entry(entry: ReflogShowEntry, format_string: str) -> str:
    """Render one entry using a compact Git-inspired placeholder subset.

    Supported placeholders are ``%H`` (new OID), ``%h`` (12-char new OID),
    ``%o`` (old OID), ``%gD`` (selector), ``%gs`` (message), ``%ct``
    (timestamp), ``%r`` (display ref), and ``%%`` (literal percent).
    """

    values = {
        "%H": entry.new_oid,
        "%h": entry.new_oid[:12],
        "%o": entry.old_oid,
        "%gD": entry.selector,
        "%gs": entry.message,
        "%ct": str(entry.timestamp),
        "%r": entry.ref,
        "%%": "%",
    }
    tokens = sorted(values, key=len, reverse=True)
    result: List[str] = []
    index = 0
    while index < len(format_string):
        if format_string[index] != "%":
            result.append(format_string[index])
            index += 1
            continue
        matched = next(
            (token for token in tokens if format_string.startswith(token, index)),
            None,
        )
        if matched is None:
            fragment = format_string[index : index + 3]
            raise ValueError(f"unsupported reflog format placeholder near {fragment!r}")
        result.append(values[matched])
        index += len(matched)
    return "".join(result)
