"""Read-only smart-HTTP remote reference inspection.

The helpers here intentionally expose the remote's native SHA-1 ref namespace.
They do not fetch packs, import objects, update tracking refs, or mutate local
configuration. pygit's internal object store remains SHA-256 based.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .remote import SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


@dataclass(frozen=True)
class RemoteRef:
    """One advertised native Git reference."""

    oid: str
    name: str


@dataclass(frozen=True)
class LsRemoteResult:
    """Filtered read-only remote advertisement result."""

    url: str
    refs: Tuple[RemoteRef, ...]
    symrefs: Tuple[Tuple[str, str], ...]


def _validate_http_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "ls-remote supports smart HTTP(S) URLs or configured remote names"
        )
    return url


def resolve_remote_url(source: str, repo: Optional[Repository] = None) -> str:
    """Resolve a configured remote name or validate a direct smart-HTTP URL."""
    parsed = urlsplit(source)
    if parsed.scheme:
        return _validate_http_url(source)
    if repo is None:
        raise KeyError(
            f"Unknown remote: {source!r}; use an HTTP(S) URL outside a repository"
        )
    try:
        url = fetch_url(repo, source)
    except KeyError as exc:
        raise KeyError(f"Unknown remote: {source!r}") from exc
    return _validate_http_url(url)


def _matches_pattern(refname: str, pattern: str) -> bool:
    """Match Git-style ls-remote patterns against the full ref or slash tails."""
    candidates = [refname]
    candidates.extend(
        refname[index + 1 :]
        for index, char in enumerate(refname)
        if char == "/"
    )
    return any(fnmatchcase(candidate, pattern) for candidate in candidates)


def ls_remote(
    source: str,
    *,
    repo: Optional[Repository] = None,
    heads: bool = False,
    tags: bool = False,
    refs_only: bool = False,
    patterns: Sequence[str] = (),
) -> LsRemoteResult:
    """Return selected refs from a remote advertisement without fetching data."""
    url = resolve_remote_url(source, repo)
    advertisement = SmartHttpClient(url).discover()

    selected = []
    for name, oid in advertisement.refs.items():
        if heads or tags:
            if not (
                (heads and name.startswith("refs/heads/"))
                or (tags and name.startswith("refs/tags/"))
            ):
                continue
        if refs_only and (name == "HEAD" or name.endswith("^{}")):
            continue
        if patterns and not any(_matches_pattern(name, pattern) for pattern in patterns):
            continue
        selected.append(RemoteRef(oid=oid, name=name))

    selected.sort(key=lambda item: item.name)
    selected_names = {item.name for item in selected}
    symrefs = tuple(
        sorted(
            (name, target)
            for name, target in advertisement.symrefs.items()
            if name in selected_names
        )
    )
    return LsRemoteResult(url=url, refs=tuple(selected), symrefs=symrefs)
