"""One-shot fetches from an explicit smart-HTTP URL.

A direct URL is not a named remote: it has no configured fetch refspec, no
remote-tracking namespace, and no persistent per-remote metadata. Objects are
still imported into the SHA-256-native store and may be recorded in FETCH_HEAD.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from .fetch_configured import (
    _advertised_sources,
    _auto_follow_tags,
    _fetch_import_sources,
)
from .fetch_head import write_fetch_head
from .fetch_policy import FetchRefspec, parse_fetch_refspec, source_is_excluded
from .fetch_porcelain import _update_destination
from .remote import SmartHttpClient
from .repo import Repository


def is_direct_fetch_url(value: str) -> bool:
    """Return whether *value* is a URL supported by pygit's smart HTTP client."""
    return urlparse(value).scheme.lower() in {"http", "https"}


def _tag_refspec() -> FetchRefspec:
    return parse_fetch_refspec("refs/tags/*:refs/tags/*")


def _mapped_destinations(
    source: str,
    command_matches: Sequence[FetchRefspec],
    refmap: Optional[Sequence[FetchRefspec]],
) -> List[Tuple[str, bool]]:
    result: List[Tuple[str, bool]] = []
    for command in command_matches:
        explicit = command.destination_for(source)
        if explicit is not None:
            result.append((explicit, command.force))
            continue
        if refmap is None:
            continue
        for mapping in refmap:
            if mapping.negative or not mapping.matches_source(source):
                continue
            destination = mapping.destination_for(source)
            if destination is not None:
                result.append((destination, command.force or mapping.force))

    unique: List[Tuple[str, bool]] = []
    for item in result:
        if item not in unique:
            unique.append(item)
    return unique


def fetch_direct_url(
    repo: Repository,
    url: str,
    *,
    refspecs: Optional[Sequence[str]] = None,
    refmap: Optional[Sequence[str]] = None,
    tags: Optional[bool] = None,
    append_fetch_head: bool = False,
    write_fetch_head: bool = True,
) -> Dict[str, object]:
    """Fetch from *url* without creating or consulting named-remote config."""
    if not is_direct_fetch_url(url):
        raise ValueError("direct fetch currently supports only http:// and https:// URLs")
    if refmap is not None and not refspecs:
        raise RuntimeError("--refmap option is only meaningful with command-line refspec(s)")

    client = SmartHttpClient(url)
    advertisement = client.discover()
    command = [parse_fetch_refspec(value) for value in (refspecs or [])]
    if command and not any(not spec.negative for spec in command):
        raise ValueError("explicit fetch refspecs require at least one positive refspec")
    mappings = None if refmap is None else [
        parse_fetch_refspec(value) for value in refmap if value.strip()
    ]

    selected: Dict[str, str] = {}
    destinations: Dict[str, List[Tuple[str, bool]]] = {}
    mergeable: List[str] = []

    if command:
        sources = _advertised_sources(advertisement)
        selection = list(command)
        if tags is True:
            selection.append(_tag_refspec())
        for refname, oid in sources.items():
            if source_is_excluded(refname, selection):
                continue
            matches = [
                spec for spec in selection
                if not spec.negative and spec.matches_source(refname)
            ]
            if not matches:
                continue
            selected[refname] = oid
            command_matches = [spec for spec in command if not spec.negative and spec.matches_source(refname)]
            if command_matches:
                mergeable.append(refname)
                destinations[refname] = _mapped_destinations(refname, command_matches, mappings)
            elif tags is True and refname.startswith("refs/tags/"):
                destinations[refname] = [(refname, False)]
    else:
        head_oid = advertisement.refs.get("HEAD")
        if head_oid:
            selected["HEAD"] = head_oid
            mergeable.append("HEAD")
        if tags is True:
            for refname, oid in _advertised_sources(advertisement).items():
                if refname.startswith("refs/tags/"):
                    selected[refname] = oid
                    destinations[refname] = [(refname, False)]

    native_map: Dict[str, str] = {}
    known_by_native: Dict[str, str] = {}
    imported, object_count = _fetch_import_sources(
        repo, client, advertisement, selected, native_map, known_by_native
    )
    for source, sha in imported.items():
        for destination, force in destinations.get(source, []):
            _update_destination(repo, destination, sha, force=force)

    if tags is not False and tags is not True:
        followed, tag_objects = _auto_follow_tags(
            repo,
            client,
            advertisement,
            native_map,
            known_by_native,
            imported.keys(),
        )
        imported.update(followed)
        object_count += tag_objects

    if write_fetch_head:
        write_fetch_head(
            repo.pygit_dir,
            imported,
            source=url,
            mergeable=mergeable,
            append=append_fetch_head,
        )
    return {
        "remote": url,
        "default_branch": None,
        "refs": imported,
        "objects": object_count,
        "pruned": [],
        "tag_mode": "all" if tags is True else "none" if tags is False else "auto",
    }
