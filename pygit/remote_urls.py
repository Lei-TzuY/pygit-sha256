"""Git-style fetch/push URL resolution and remote URL mutation helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Tuple

from .config import GitConfig
from .repo import Repository


def _legacy_url(repo: Repository, remote: str) -> str:
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    value = str(settings.get("url") or "").strip()
    if not value:
        raise RuntimeError(f"Remote '{remote}' has no URL configured")
    return value


def configured_remote_urls(repo: Repository, remote: str, *, push: bool = False) -> Tuple[str, ...]:
    """Return explicitly configured URL values without fallback semantics."""
    _legacy_url(repo, remote)  # validate the named remote
    key = "pushurl" if push else "url"
    values = GitConfig(repo.pygit_dir).get_all("remote", f"{remote}.{key}")
    return tuple(value for value in (item.strip() for item in values) if value)


def fetch_urls(repo: Repository, remote: str) -> Tuple[str, ...]:
    """Return ordered fetch URLs; Git fetches only from the first value."""
    explicit = configured_remote_urls(repo, remote, push=False)
    return explicit or (_legacy_url(repo, remote),)


def fetch_url(repo: Repository, remote: str) -> str:
    """Return the single URL Git would use for fetch/ls-remote operations."""
    return fetch_urls(repo, remote)[0]


def push_urls(repo: Repository, remote: str) -> Tuple[str, ...]:
    """Return ordered push URLs, falling back to all ordinary remote URLs."""
    explicit = configured_remote_urls(repo, remote, push=True)
    return explicit or fetch_urls(repo, remote)


def get_remote_urls(repo: Repository, remote: str, *, push: bool = False, all_urls: bool = False) -> Tuple[str, ...]:
    """Return URLs for ``remote get-url`` presentation."""
    values = push_urls(repo, remote) if push else fetch_urls(repo, remote)
    return values if all_urls else values[:1]


def _entry_key(raw_line: str) -> str | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith(("#", ";", "[")):
        return None
    if "=" in raw_line:
        return raw_line.split("=", 1)[0].strip().lower()
    return stripped.split(None, 1)[0].lower()


def _replace_multivar(repo: Repository, remote: str, key: str, values: Iterable[str]) -> None:
    """Replace one flattened ``[remote]`` multi-valued key in source order."""
    path = repo.pygit_dir / "config"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    target = f"{remote}.{key}".lower()
    kept = []
    current_section: str | None = None
    remote_header_index: int | None = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip().lower()
            kept.append(raw_line)
            if current_section == "remote":
                remote_header_index = len(kept) - 1
            continue
        if current_section == "remote" and _entry_key(raw_line) == target:
            continue
        kept.append(raw_line)

    new_lines = [f"{remote}.{key} = {value}" for value in values]
    if remote_header_index is None:
        if kept and kept[-1].strip():
            kept.append("")
        kept.append("[remote]")
        remote_header_index = len(kept) - 1

    insert_at = remote_header_index + 1
    kept[insert_at:insert_at] = new_lines
    text = "\n".join(kept)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def set_remote_url(
    repo: Repository,
    remote: str,
    url: str,
    *,
    old_url: str | None = None,
    push: bool = False,
    add: bool = False,
    delete: bool = False,
) -> Tuple[str, ...]:
    """Apply Git-style ``remote set-url`` mutation and return explicit values."""
    if add and delete:
        raise ValueError("--add and --delete are mutually exclusive")
    if "\n" in url or "\x00" in url:
        raise ValueError("remote URL cannot contain NUL or newline")

    key = "pushurl" if push else "url"
    explicit = list(configured_remote_urls(repo, remote, push=push))
    # Ordinary URLs retain pygit's historical config.json value as an implicit
    # first entry until the first set-url mutation materializes the list.
    values = explicit if (push or explicit) else list(fetch_urls(repo, remote))

    if add:
        if old_url is not None:
            raise ValueError("--add does not accept an old URL pattern")
        values.append(url)
    elif delete:
        if old_url is not None:
            raise ValueError("--delete accepts one URL regex, not an old URL")
        try:
            pattern = re.compile(url)
        except re.error as exc:
            raise ValueError(f"invalid URL regex '{url}': {exc}") from exc
        matched = [value for value in values if pattern.search(value)]
        if not matched:
            raise RuntimeError(f"No such URL found: {url}")
        remaining = [value for value in values if not pattern.search(value)]
        if not push and not remaining:
            raise RuntimeError("Will not delete all non-push URLs")
        values = remaining
    else:
        if old_url is None:
            if values:
                values[0] = url
            else:
                values = [url]
        else:
            try:
                pattern = re.compile(old_url)
            except re.error as exc:
                raise ValueError(f"invalid URL regex '{old_url}': {exc}") from exc
            index = next((i for i, value in enumerate(values) if pattern.search(value)), None)
            if index is None:
                raise RuntimeError(f"No such URL found: {old_url}")
            values[index] = url

    _replace_multivar(repo, remote, key, values)
    return tuple(values)
