"""Git-style porcelain v2 status records.

This layer reuses Phase 150's normalized status/conflict model and enriches it
with the index/HEAD metadata required by porcelain v2. The repository remains
SHA-256-native, so object-name fields are 64 hexadecimal characters rather
than Git's SHA-1-width examples. Phase 152 adds the optional stash-count header
using the repository's strict reflog reader. Phase 154 threads Git's untracked
path modes through the same normalized presentation layer. Phase 159 adds
porcelain-v2 type-2 records for staged renames.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

from .index import _mode_for
from .reflog_show import show_reflog
from .repo import Repository


_ZERO_OID = "0" * 64
_ZERO_MODE = "000000"


def _quote_path(path: str) -> str:
    """Return a stable C-style quoted pathname when quoting is required."""
    if path and all(0x20 <= ord(ch) < 0x7f and ch not in {'"', '\\'} for ch in path):
        return path
    return json.dumps(path, ensure_ascii=False)


def _path_field(path: str, *, nul: bool) -> str:
    return path if nul else _quote_path(path)


def _worktree_mode(repo: Repository, path: str) -> str:
    candidate = repo.worktree / path
    try:
        if candidate.exists() or candidate.is_symlink():
            return _mode_for(candidate)
    except OSError:
        pass
    return _ZERO_MODE


def _head_entries(repo: Repository) -> dict:
    head = repo.refs.resolve_head()
    if not head:
        return {}
    return repo._commit_tree_entries(head)


def _branch_headers(repo: Repository, result: dict) -> List[str]:
    head_oid = repo.refs.resolve_head()
    branch = repo.refs.current_branch()
    lines = [
        f"# branch.oid {head_oid or '(initial)'}",
        f"# branch.head {branch or '(detached)'}",
    ]
    upstream = result.get("upstream")
    if isinstance(upstream, dict) and upstream.get("upstream"):
        lines.append(f"# branch.upstream {upstream['upstream']}")
        if head_oid:
            lines.append(
                f"# branch.ab +{int(upstream.get('ahead') or 0)} "
                f"-{int(upstream.get('behind') or 0)}"
            )
    return lines


def stash_count(repo: Repository) -> int:
    """Return the number of stash entries from ``refs/stash``'s reflog."""
    return len(show_reflog(repo, "stash"))


def porcelain_v2_lines(
    repo: Repository,
    *,
    branch: bool = False,
    ignored: bool = False,
    nul: bool = False,
    show_stash: bool = False,
    untracked_mode: str = "normal",
    renames: bool = True,
    rename_threshold: int = 50,
) -> List[str]:
    """Return porcelain-v2 headers and records without final terminators."""
    from .status_cli import _normalized_status, status_records

    result, unmerged = _normalized_status(
        repo,
        ignored=ignored,
        untracked_mode=untracked_mode,
    )
    lines: List[str] = []
    if branch:
        lines.extend(_branch_headers(repo, result))
    if show_stash:
        count = stash_count(repo)
        if count:
            lines.append(f"# stash {count}")

    head_entries = _head_entries(repo)
    unmerged_by_path = {entry.path: entry for entry in unmerged}

    for record in status_records(
        repo,
        ignored=ignored,
        untracked_mode=untracked_mode,
        renames=renames,
        rename_threshold=rename_threshold,
    ):
        path = record.path
        if record.code in {"??", "!!"}:
            prefix = "?" if record.code == "??" else "!"
            lines.append(f"{prefix} {_path_field(path, nul=nul)}")
            continue

        conflict = unmerged_by_path.get(path)
        if conflict is not None:
            stages = {stage: repo.index.get(path, stage) for stage in (1, 2, 3)}
            modes = [
                stages[stage].mode if stages[stage] is not None else _ZERO_MODE
                for stage in (1, 2, 3)
            ]
            oids = [
                stages[stage].sha if stages[stage] is not None else _ZERO_OID
                for stage in (1, 2, 3)
            ]
            lines.append(
                "u "
                f"{record.code} N... {modes[0]} {modes[1]} {modes[2]} "
                f"{_worktree_mode(repo, path)} {oids[0]} {oids[1]} {oids[2]} "
                f"{_path_field(path, nul=nul)}"
            )
            continue

        if record.orig_path is not None:
            source = record.orig_path
            head_entry = head_entries.get(source)
            index_entry = repo.index.get(path)
            if head_entry is None or index_entry is None:
                raise RuntimeError(
                    "rename metadata disappeared while rendering status: "
                    f"{source!r} -> {path!r}"
                )
            head_oid, head_mode = head_entry
            xy = record.code.replace(" ", ".")
            score = record.score if record.score is not None else 100
            separator = "\0" if nul else "\t"
            lines.append(
                "2 "
                f"{xy} N... {head_mode} {index_entry.mode} "
                f"{_worktree_mode(repo, path)} {head_oid} {index_entry.sha} "
                f"R{score} {_path_field(path, nul=nul)}{separator}"
                f"{_path_field(source, nul=nul)}"
            )
            continue

        index_entry = repo.index.get(path)
        head_entry = head_entries.get(path)
        head_oid = head_entry[0] if head_entry else _ZERO_OID
        head_mode = head_entry[1] if head_entry else _ZERO_MODE
        index_oid = index_entry.sha if index_entry else _ZERO_OID
        index_mode = index_entry.mode if index_entry else _ZERO_MODE
        xy = record.code.replace(" ", ".")
        lines.append(
            "1 "
            f"{xy} N... {head_mode} {index_mode} {_worktree_mode(repo, path)} "
            f"{head_oid} {index_oid} {_path_field(path, nul=nul)}"
        )

    return lines


def render_porcelain_v2(
    repo: Repository,
    *,
    branch: bool = False,
    ignored: bool = False,
    nul: bool = False,
    show_stash: bool = False,
    untracked_mode: str = "normal",
    renames: bool = True,
    rename_threshold: int = 50,
) -> str:
    """Render a complete porcelain-v2 stream."""
    lines = porcelain_v2_lines(
        repo,
        branch=branch,
        ignored=ignored,
        nul=nul,
        show_stash=show_stash,
        untracked_mode=untracked_mode,
        renames=renames,
        rename_threshold=rename_threshold,
    )
    if not lines:
        return ""
    separator = "\0" if nul else "\n"
    return separator.join(lines) + separator
