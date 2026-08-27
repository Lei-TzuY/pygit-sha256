"""Git-style ``remote set-branches`` configuration mutation.

Phase182 exposes the fetch-refspec list that Phase181 already honors.  The
operation is intentionally metadata-only: it changes which remote branches a
future configured fetch/pull will select, but it neither fetches immediately
nor deletes already-existing remote-tracking refs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .repo import Repository


def _config_path(repo: Repository) -> Path:
    return repo.pygit_dir / "config"


def _read_lines(repo: Repository) -> List[str]:
    path = _config_path(repo)
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _write_lines(repo: Repository, lines: Iterable[str]) -> None:
    data = list(lines)
    text = "\n".join(data)
    if text:
        text += "\n"
    _config_path(repo).write_text(text, encoding="utf-8")


def _section(raw_line: str) -> Optional[str]:
    stripped = raw_line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip().lower()
    return None


def _entry(raw_line: str) -> Optional[Tuple[str, str]]:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith(("#", ";", "[")):
        return None
    if "=" in raw_line:
        key, value = raw_line.split("=", 1)
        return key.strip(), value.strip()
    parts = stripped.split(None, 1)
    return parts[0], parts[1].strip() if len(parts) == 2 else ""


def _validate_branch(branch: str) -> None:
    # Native `git remote set-branches` treats each BRANCH token literally when
    # constructing the same refspec as `remote add -t`; it does not validate a
    # conventional branch name here. Keep that permissiveness while refusing
    # bytes that cannot be represented safely in this text configuration.
    if "\x00" in branch or "\n" in branch or "\r" in branch:
        raise ValueError("tracked branch token must not contain NUL or newline")


def _fetch_refspec(remote: str, branch: str) -> str:
    return f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"


def set_remote_branches(
    repo: Repository,
    remote: str,
    branches: Iterable[str],
    *,
    add: bool = False,
) -> List[str]:
    """Replace or append ``remote.<name>.fetch`` branch mappings.

    Git's ``remote set-branches`` is equivalent to re-applying ``remote add -t``
    branch selections. Duplicate branch arguments are preserved, ``--add``
    appends rather than deduplicating, and an empty replacement list removes all
    configured fetch mappings. Existing tracking refs are deliberately left in
    place until a later prune/removal operation.
    """
    if remote not in repo.list_remotes():
        raise KeyError(f"No such remote: '{remote}'")

    branch_list = list(branches)
    for branch in branch_list:
        _validate_branch(branch)
    additions = [_fetch_refspec(remote, branch) for branch in branch_list]

    lines = _read_lines(repo)
    target_key = f"{remote}.fetch".lower()
    current: Optional[str] = None
    matching: List[int] = []
    remote_header: Optional[int] = None
    remote_end = len(lines)

    for index, raw in enumerate(lines):
        sec = _section(raw)
        if sec is not None:
            if current == "remote" and remote_end == len(lines):
                remote_end = index
            current = sec
            if sec == "remote" and remote_header is None:
                remote_header = index
            continue
        item = _entry(raw)
        if current == "remote" and item and item[0].lower() == target_key:
            matching.append(index)

    if remote_header is None:
        # Lifecycle-managed remotes should already have a [remote] section, but
        # keep this robust for repositories created by older pygit versions.
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[remote]")
        remote_header = len(lines) - 1
        remote_end = len(lines)

    rendered = [f"{remote}.fetch = {value}" for value in additions]
    if add:
        if not rendered:
            return []
        insert_at = matching[-1] + 1 if matching else remote_end
        lines[insert_at:insert_at] = rendered
    else:
        insert_at = matching[0] if matching else remote_end
        matching_set = set(matching)
        lines = [line for index, line in enumerate(lines) if index not in matching_set]
        removed_before = sum(1 for index in matching if index < insert_at)
        insert_at -= removed_before
        lines[insert_at:insert_at] = rendered

    _write_lines(repo, lines)
    return additions
