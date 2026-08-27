"""Git-style remote add/remove/rename lifecycle synchronization.

pygit historically stores transport endpoints in ``.pygit/config.json`` while
newer Git-compatible configuration lives in the INI-style ``.pygit/config``.
Phase179 keeps both representations coherent for user-facing remote lifecycle
operations without changing the long-standing :class:`Repository` APIs.
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


def _remote_prefix_present(repo: Repository, name: str) -> bool:
    current: Optional[str] = None
    prefix = f"{name}.".lower()
    for line in _read_lines(repo):
        sec = _section(line)
        if sec is not None:
            current = sec
            continue
        item = _entry(line)
        if current == "remote" and item and item[0].lower().startswith(prefix):
            return True
    return False


def _append_remote_defaults(repo: Repository, name: str, url: str) -> None:
    lines = _read_lines(repo)
    remote_header: Optional[int] = None
    for index, line in enumerate(lines):
        if _section(line) == "remote":
            remote_header = index
            break

    additions = [
        f"{name}.url = {url}",
        f"{name}.fetch = +refs/heads/*:refs/remotes/{name}/*",
    ]
    if remote_header is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[remote]")
        lines.extend(additions)
    else:
        lines[remote_header + 1 : remote_header + 1] = additions
    _write_lines(repo, lines)


def _rewrite_entry(key: str, value: str) -> str:
    return f"{key} = {value}"


def _rename_ini_remote(repo: Repository, old: str, new: str) -> None:
    lines = _read_lines(repo)
    current: Optional[str] = None
    old_prefix = f"{old}.".lower()
    result: List[str] = []

    for raw in lines:
        sec = _section(raw)
        if sec is not None:
            current = sec
            result.append(raw)
            continue
        item = _entry(raw)
        if item is None:
            result.append(raw)
            continue
        key, value = item
        lower_key = key.lower()

        if current == "remote":
            if lower_key.startswith(old_prefix):
                suffix = key[len(old) :]
                new_key = new + suffix
                if lower_key == f"{old}.fetch".lower():
                    value = value.replace(
                        f"refs/remotes/{old}/",
                        f"refs/remotes/{new}/",
                    )
                result.append(_rewrite_entry(new_key, value))
                continue
            if lower_key == "pushdefault" and value == old:
                result.append(_rewrite_entry(key, new))
                continue

        if current == "branch" and (
            lower_key.endswith(".remote") or lower_key.endswith(".pushremote")
        ) and value == old:
            result.append(_rewrite_entry(key, new))
            continue

        result.append(raw)

    _write_lines(repo, result)


def _remove_ini_remote(repo: Repository, name: str) -> None:
    lines = _read_lines(repo)
    current: Optional[str] = None
    prefix = f"{name}.".lower()

    # Git removes branch.<name>.merge together with branch.<name>.remote when
    # that upstream points at the remote being removed.  Determine those
    # branch key prefixes before filtering the file.
    tracked_branches = set()
    for raw in lines:
        sec = _section(raw)
        if sec is not None:
            current = sec
            continue
        item = _entry(raw)
        if current != "branch" or item is None:
            continue
        key, value = item
        lower_key = key.lower()
        if lower_key.endswith(".remote") and value == name:
            tracked_branches.add(lower_key[: -len(".remote")])

    current = None
    result: List[str] = []
    for raw in lines:
        sec = _section(raw)
        if sec is not None:
            current = sec
            result.append(raw)
            continue
        item = _entry(raw)
        if item is None:
            result.append(raw)
            continue
        key, value = item
        lower_key = key.lower()

        if current == "remote":
            if lower_key.startswith(prefix):
                continue
            if lower_key == "pushdefault" and value == name:
                continue

        if current == "branch":
            if lower_key.endswith(".pushremote") and value == name:
                continue
            branch_prefix = lower_key.rsplit(".", 1)[0] if "." in lower_key else ""
            if branch_prefix in tracked_branches and lower_key in {
                f"{branch_prefix}.remote",
                f"{branch_prefix}.merge",
            }:
                continue

        result.append(raw)

    _write_lines(repo, result)


def add_remote(repo: Repository, name: str, url: str) -> None:
    """Add a named remote to both legacy and Git-style configuration."""
    if not name or any(char.isspace() for char in name):
        raise ValueError("remote name must be non-empty and contain no whitespace")
    if not url or "\n" in url or "\x00" in url:
        raise ValueError("remote URL must be non-empty and contain no NUL/newline")
    if name in repo.list_remotes() or _remote_prefix_present(repo, name):
        raise RuntimeError(f"Remote already exists: '{name}'")

    repo.add_remote(name, url)
    _append_remote_defaults(repo, name, url)


def rename_remote(repo: Repository, old: str, new: str) -> None:
    """Rename a remote and every Git-style config reference Git rewrites."""
    if not old or not new:
        raise ValueError("remote rename requires non-empty OLD and NEW names")
    if old == new:
        if old not in repo.list_remotes():
            raise KeyError(f"Unknown remote: '{old}'")
        return
    if new in repo.list_remotes() or _remote_prefix_present(repo, new):
        raise RuntimeError(f"Remote already exists: '{new}'")

    repo.rename_remote(old, new)
    _rename_ini_remote(repo, old, new)


def remove_remote(repo: Repository, name: str) -> None:
    """Remove a remote and its Git-style configuration references."""
    repo.remove_remote(name)
    _remove_ini_remote(repo, name)
