"""Git-style ``fetch --prefetch`` orchestration.

The option rewrites configured fetch destinations into ``refs/prefetch/`` while
leaving explicit command destinations alone.  Transport and object conversion
remain delegated to the established fetch porcelain.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .fetch_head import write_fetch_head
from .fetch_policy import configured_fetch_refspecs, parse_fetch_refspec, source_is_excluded
from .fetch_porcelain import fetch_porcelain
from .fetch_prefetch import (
    delete_prefetch_ref,
    list_prefetch_refs,
    prefetch_refspecs,
    set_prefetch_ref,
)
from .remote import SmartHttpClient
from .remote_urls import fetch_url


def _source_only(spec) -> str:
    if spec.negative:
        return "^" + spec.source
    return spec.source


def _prefetch_prune(repo, advertisement, mappings) -> list[str]:
    advertised = {
        name
        for name in advertisement.refs
        if name != "HEAD" and not name.endswith("^{}")
    }
    pruned: list[str] = []
    for destination in list_prefetch_refs(repo):
        sources = []
        for spec in mappings:
            if spec.negative:
                continue
            source = spec.source_for_destination(destination)
            if source is None or source_is_excluded(source, mappings):
                continue
            sources.append(source)
        if sources and not any(source in advertised for source in sources):
            delete_prefetch_ref(repo, destination)
            pruned.append(destination)
    return pruned


def _write_prefetch_destinations(repo, imported, mappings, command_specs=None) -> None:
    command_specs = list(command_specs or [])
    for source, sha in imported.items():
        if source_is_excluded(source, mappings):
            continue
        if command_specs:
            matches = [
                spec
                for spec in command_specs
                if not spec.negative and spec.matches_source(source)
            ]
            if not matches or all(spec.destination is not None for spec in matches):
                continue
        for mapping in mappings:
            if mapping.negative or not mapping.matches_source(source):
                continue
            destination = mapping.destination_for(source)
            if destination is not None:
                set_prefetch_ref(repo, destination, sha)


def fetch_prefetched(
    repo,
    remote: str,
    *,
    refspecs: Optional[Sequence[str]] = None,
    refmap: Optional[Sequence[str]] = None,
    force: bool = False,
    prune: Optional[bool] = None,
    prune_tags: Optional[bool] = None,
    tags: Optional[bool] = None,
    append_fetch_head: bool = False,
    write_fetch_head_enabled: bool = True,
):
    """Fetch a named remote with configured destinations redirected to prefetch refs."""
    configured = configured_fetch_refspecs(repo, remote)
    mappings = prefetch_refspecs(configured)

    # An explicit --refmap replaces configured mappings, so --prefetch has no
    # configured destination left to rewrite. Preserve ordinary porcelain.
    if refmap is not None:
        return fetch_porcelain(
            repo,
            remote,
            refspecs=refspecs,
            refmap=refmap,
            force=force,
            prune=prune,
            prune_tags=prune_tags,
            tags=tags,
            append_fetch_head=append_fetch_head,
            write_fetch_head=write_fetch_head_enabled,
        )

    url = fetch_url(repo, remote)
    advertisement = SmartHttpClient(url).discover()
    command_specs = [parse_fetch_refspec(value) for value in refspecs or []]

    if refspecs:
        transport_refspecs = list(refspecs)
    else:
        transport_refspecs = [_source_only(spec) for spec in configured]

    # Disable the configured destination map inside the existing explicit
    # fetch path. Explicit command destinations still apply; configured
    # destinations are materialized below under refs/prefetch/.
    result = fetch_porcelain(
        repo,
        remote,
        refspecs=transport_refspecs or None,
        refmap=[] if transport_refspecs else None,
        force=force,
        prune=False if prune is not None else None,
        prune_tags=prune_tags,
        tags=tags,
        append_fetch_head=append_fetch_head,
        write_fetch_head=False,
    )

    pruned = _prefetch_prune(repo, advertisement, mappings) if prune is True else []
    _write_prefetch_destinations(
        repo,
        result.get("refs", {}),
        mappings,
        command_specs=command_specs if refspecs else None,
    )

    default_ref = advertisement.symrefs.get("HEAD")
    default_branch = (
        default_ref[len("refs/heads/") :]
        if default_ref and default_ref.startswith("refs/heads/")
        else repo._infer_default_branch(advertisement.refs)
    )
    result["default_branch"] = default_branch
    result["pruned"] = pruned

    if write_fetch_head_enabled:
        merge_ref = f"refs/heads/{default_branch}" if default_branch else None
        mergeable = [merge_ref] if merge_ref in result.get("refs", {}) else []
        write_fetch_head(
            repo.pygit_dir,
            result.get("refs", {}),
            source=url,
            mergeable=mergeable,
            append=append_fetch_head,
        )
    return result
