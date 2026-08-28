"""Porcelain fetch orchestration for explicit refspecs and FETCH_HEAD."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .fetch_configured import (
    _advertised_sources,
    _auto_follow_tags,
    _fetch_import_sources,
    _prune_refs,
    _selection_specs,
    _select_explicit_sources,
    fetch_configured,
)
from .fetch_head import write_fetch_head as _write_fetch_head
from .fetch_policy import (
    FetchRefspec,
    configured_fetch_refspecs,
    parse_fetch_refspec,
    resolve_fetch_policy,
    source_is_excluded,
)
from .objects import CommitObject
from .remote import SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def _is_ancestor(repo: Repository, old: str, new: str) -> bool:
    if old == new:
        return True
    pending = [new]
    seen = set()
    while pending:
        oid = pending.pop()
        if oid in seen:
            continue
        seen.add(oid)
        if oid == old:
            return True
        try:
            obj = repo.store.read(oid)
        except Exception:
            continue
        if isinstance(obj, CommitObject):
            pending.extend(obj.parents)
    return False


def _update_destination(repo: Repository, destination: str, sha: str, *, force: bool) -> None:
    if destination.startswith("refs/remotes/"):
        remainder = destination[len("refs/remotes/") :]
        if "/" not in remainder:
            raise ValueError(f"invalid remote-tracking destination: {destination!r}")
        remote, branch = remainder.split("/", 1)
        repo.refs.set_remote(remote, branch, sha)
        return
    if destination.startswith("refs/tags/"):
        name = destination[len("refs/tags/") :]
        current = repo.refs.get_tag(name)
        if current is not None and current != sha and not force:
            raise RuntimeError(f"fetch rejected: tag '{name}' would clobber existing tag")
        repo.refs.set_tag(name, sha)
        return
    if destination.startswith("refs/heads/"):
        name = destination[len("refs/heads/") :]
        current = repo.refs.get_branch(name)
        if current is not None and current != sha and not force and not _is_ancestor(repo, current, sha):
            raise RuntimeError(f"fetch rejected: branch '{name}' would not fast-forward")
        repo.refs.set_branch(name, sha, message="fetch")
        return
    raise ValueError(f"unsupported fetch destination: {destination!r}")


def _mapped_destinations(
    source: str,
    mappings: Sequence[FetchRefspec],
    command_matches: Sequence[FetchRefspec],
) -> List[Tuple[str, bool]]:
    result: List[Tuple[str, bool]] = []
    for command in command_matches:
        destination = command.destination_for(source)
        if destination is not None:
            result.append((destination, command.force))
            continue
        for mapping in mappings:
            if mapping.negative or not mapping.matches_source(source):
                continue
            mapped = mapping.destination_for(source)
            if mapped is not None:
                result.append((mapped, command.force or mapping.force))
    unique: List[Tuple[str, bool]] = []
    for item in result:
        if item not in unique:
            unique.append(item)
    return unique


def _explicit_fetch(
    repo: Repository,
    remote: str,
    refspecs: Sequence[str],
    *,
    refmap: Optional[Sequence[str]],
    prune: Optional[bool],
    prune_tags: Optional[bool],
    tags: Optional[bool],
    append_fetch_head: bool,
    write_fetch_head_enabled: bool,
) -> Dict[str, object]:
    url = fetch_url(repo, remote)
    client = SmartHttpClient(url)
    advertisement = client.discover()
    policy = resolve_fetch_policy(repo, remote, prune=prune, prune_tags=prune_tags, tags=tags)
    if refmap is None:
        mappings = configured_fetch_refspecs(repo, remote)
    else:
        mappings = [parse_fetch_refspec(value) for value in refmap if value.strip()]

    command = [parse_fetch_refspec(value) for value in refspecs]
    if command and not any(not spec.negative for spec in command):
        raise ValueError("explicit fetch refspecs require at least one positive refspec")

    selection_specs, prune_specs = _selection_specs(command, policy)
    pruned = _prune_refs(repo, remote, advertisement, prune_specs) if policy.prune else []
    sources = _advertised_sources(advertisement)
    selected: Dict[str, str] = {}
    destinations: Dict[str, List[Tuple[str, bool]]] = {}
    for refname, oid in sources.items():
        if source_is_excluded(refname, selection_specs):
            continue
        matches = [s for s in selection_specs if not s.negative and s.matches_source(refname)]
        if not matches:
            continue
        selected[refname] = oid
        destinations[refname] = _mapped_destinations(refname, mappings, matches)

    native_map = repo._read_native_map(remote)
    known_by_native = {native: sha for sha, native in native_map.items()}
    imported, object_count = _fetch_import_sources(
        repo, client, advertisement, selected, native_map, known_by_native
    )
    repo._write_native_map(native_map, remote)
    for source, sha in imported.items():
        for destination, force in destinations.get(source, []):
            _update_destination(repo, destination, sha, force=force)

    if policy.tag_mode == "auto" and not policy.prune_tags:
        followed, tag_objects = _auto_follow_tags(
            repo, client, advertisement, native_map, known_by_native, imported.keys()
        )
        if followed:
            imported.update(followed)
            repo._write_native_map(native_map, remote)
        object_count += tag_objects

    if write_fetch_head_enabled:
        _write_fetch_head(
            repo.pygit_dir,
            imported,
            source=url,
            mergeable=[name for name in selected if name in imported],
            append=append_fetch_head,
        )
    return {
        "remote": remote,
        "default_branch": None,
        "refs": imported,
        "objects": object_count,
        "pruned": pruned,
        "tag_mode": policy.tag_mode,
    }


def fetch_porcelain(
    repo: Repository,
    remote: str = "origin",
    *,
    refspecs: Optional[Sequence[str]] = None,
    refmap: Optional[Sequence[str]] = None,
    prune: Optional[bool] = None,
    prune_tags: Optional[bool] = None,
    tags: Optional[bool] = None,
    append_fetch_head: bool = False,
    write_fetch_head: bool = True,
) -> Dict[str, object]:
    """Fetch like Git porcelain, including explicit refspec/refmap and FETCH_HEAD rules."""
    if refmap is not None and not refspecs:
        raise RuntimeError("--refmap option is only meaningful with command-line refspec(s)")
    if refspecs:
        return _explicit_fetch(
            repo,
            remote,
            refspecs,
            refmap=refmap,
            prune=prune,
            prune_tags=prune_tags,
            tags=tags,
            append_fetch_head=append_fetch_head,
            write_fetch_head_enabled=write_fetch_head,
        )

    result = fetch_configured(
        repo, remote, prune=prune, prune_tags=prune_tags, tags=tags
    )
    if write_fetch_head:
        url = fetch_url(repo, remote)
        default = result.get("default_branch")
        default_ref = f"refs/heads/{default}" if default else None
        mergeable = [default_ref] if default_ref in result["refs"] else []
        _write_fetch_head(
            repo.pygit_dir,
            result["refs"],
            source=url,
            mergeable=mergeable,
            append=append_fetch_head,
        )
    return result
