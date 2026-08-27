"""Configured fetch transport with Git-style pruning and tag policy.

Phase181 made clone-generated branch mappings operational.  Phase182 layers
fetch pruning and tag policy on top while keeping the legacy Repository.fetch
API unchanged for callers that still depend on its historical behavior.
"""

from __future__ import annotations

import fnmatch
from typing import Dict, List, Optional, Sequence, Tuple

from .fetch_policy import (
    FetchPolicy,
    FetchRefspec,
    configured_fetch_refspecs as _parsed_fetch_refspecs,
    parse_fetch_refspec,
    resolve_fetch_policy,
    source_is_excluded,
)
from .remote import Advertisement, NativeImporter, SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def configured_fetch_refspecs(repo: Repository, remote: str) -> List[str]:
    """Compatibility wrapper returning raw configured/default refspec strings."""
    return [spec.raw for spec in _parsed_fetch_refspecs(repo, remote)]


def _source_pattern(raw: str) -> tuple[bool, str]:
    """Phase181 compatibility helper retained for selector callers."""
    spec = parse_fetch_refspec(raw)
    return spec.negative, spec.source


def _matches(refname: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(refname, pattern) if "*" in pattern else refname == pattern


def select_fetch_import_refs(
    repo: Repository,
    remote: str,
    native_refs: Dict[str, str],
) -> Dict[str, str]:
    """Phase181-compatible source selector.

    Existing callers of this helper retain the old behavior where all tags are
    admitted.  The Phase182 transport path below applies the more precise tag
    policy instead of using this compatibility function directly.
    """
    positives: List[str] = []
    negatives: List[str] = []
    for raw in configured_fetch_refspecs(repo, remote):
        negative, pattern = _source_pattern(raw)
        (negatives if negative else positives).append(pattern)

    result: Dict[str, str] = {}
    for refname, oid in native_refs.items():
        if refname == "HEAD":
            continue
        if not refname.startswith("refs/heads/"):
            result[refname] = oid
            continue
        selected = any(_matches(refname, pattern) for pattern in positives)
        if selected and not any(_matches(refname, pattern) for pattern in negatives):
            result[refname] = oid
    return result


def _advertised_sources(advertisement: Advertisement) -> Dict[str, str]:
    return {
        name: oid
        for name, oid in advertisement.refs.items()
        if name != "HEAD" and not name.endswith("^{}")
    }


def _tag_refspec(*, force: bool) -> FetchRefspec:
    prefix = "+" if force else ""
    return parse_fetch_refspec(f"{prefix}refs/tags/*:refs/tags/*")


def _selection_specs(
    configured: Sequence[FetchRefspec],
    policy: FetchPolicy,
) -> Tuple[List[FetchRefspec], List[FetchRefspec]]:
    """Return (selection specs, prune-domain specs)."""
    selected = list(configured)
    prune_domain = list(configured)

    if policy.prune_tags:
        # Git's --prune-tags acts like an explicit tag refspec and permits tags
        # to follow the remote value while pruning is enabled.
        tag_spec = _tag_refspec(force=True)
        selected.append(tag_spec)
        prune_domain.append(tag_spec)
    elif policy.tag_mode == "all":
        # --tags fetches every tag, but --prune --tags alone does not make tags
        # part of the pruning domain.
        selected.append(_tag_refspec(force=False))

    return selected, prune_domain


def _select_explicit_sources(
    advertisement: Advertisement,
    specs: Sequence[FetchRefspec],
) -> Tuple[Dict[str, str], Dict[str, List[Tuple[str, bool]]]]:
    sources = _advertised_sources(advertisement)
    selected: Dict[str, str] = {}
    destinations: Dict[str, List[Tuple[str, bool]]] = {}
    spec_list = list(specs)

    for refname, oid in sources.items():
        if source_is_excluded(refname, spec_list):
            continue
        matches = [
            spec
            for spec in spec_list
            if not spec.negative and spec.matches_source(refname)
        ]
        if not matches:
            continue
        selected[refname] = oid
        for spec in matches:
            destination = spec.destination_for(refname)
            if destination is not None:
                destinations.setdefault(refname, []).append((destination, spec.force))
    return selected, destinations


def _fetch_import_sources(
    repo: Repository,
    client: SmartHttpClient,
    advertisement: Advertisement,
    source_oids: Dict[str, str],
    native_map: Dict[str, str],
    known_by_native: Dict[str, str],
) -> Tuple[Dict[str, str], int]:
    if not source_oids:
        return {}, 0

    if all(oid in known_by_native for oid in source_oids.values()):
        return {name: known_by_native[oid] for name, oid in source_oids.items()}, 0

    selected_advertisement = Advertisement(
        refs=dict(source_oids),
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )
    result = client.fetch(
        haves=native_map.values(),
        advertisement=selected_advertisement,
    )
    importer = NativeImporter(repo.store, result.objects, known=known_by_native)
    imported = {
        ref_name: importer.import_oid(native_oid)
        for ref_name, native_oid in source_oids.items()
    }
    known_by_native.update(importer.converted)
    native_map.update(
        {
            pygit_sha: native_oid
            for native_oid, pygit_sha in importer.converted.items()
        }
    )
    repo._write_native_map(native_map, "__PHASE182_REMOTE_PLACEHOLDER__")
    return imported, len(result.objects)


def _write_native_map(repo: Repository, remote: str, native_map: Dict[str, str]) -> None:
    """Write a remote native map without leaking the helper's placeholder."""
    repo._write_native_map(native_map, remote)
    placeholder = repo.pygit_dir / "native-map-__PHASE182_REMOTE_PLACEHOLDER__.json"
    if placeholder.exists():
        placeholder.unlink()


def _update_destination(
    repo: Repository,
    destination: str,
    sha: str,
    *,
    force: bool,
) -> None:
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

    raise ValueError(f"unsupported fetch destination: {destination!r}")


def _apply_destinations(
    repo: Repository,
    imported: Dict[str, str],
    destinations: Dict[str, List[Tuple[str, bool]]],
) -> None:
    for source, sha in imported.items():
        for destination, force in destinations.get(source, []):
            _update_destination(repo, destination, sha, force=force)


def _auto_follow_tags(
    repo: Repository,
    client: SmartHttpClient,
    advertisement: Advertisement,
    native_map: Dict[str, str],
    known_by_native: Dict[str, str],
    already_selected: Sequence[str],
) -> Tuple[Dict[str, str], int]:
    selected = set(already_selected)
    immediate: Dict[str, str] = {}
    needs_object: Dict[str, str] = {}

    for refname, tag_oid in _advertised_sources(advertisement).items():
        if not refname.startswith("refs/tags/") or refname in selected:
            continue
        tag_name = refname[len("refs/tags/") :]
        # Automatic tag following never clobbers an existing local tag.
        if repo.refs.get_tag(tag_name) is not None:
            continue
        peeled = advertisement.refs.get(f"{refname}^{{}}", tag_oid)
        if peeled not in known_by_native:
            continue
        if tag_oid in known_by_native:
            immediate[refname] = known_by_native[tag_oid]
        else:
            needs_object[refname] = tag_oid

    fetched: Dict[str, str] = {}
    object_count = 0
    if needs_object:
        tag_advertisement = Advertisement(
            refs=dict(needs_object),
            capabilities=set(advertisement.capabilities),
            symrefs={},
        )
        result = client.fetch(
            haves=native_map.values(),
            advertisement=tag_advertisement,
        )
        importer = NativeImporter(repo.store, result.objects, known=known_by_native)
        fetched = {
            refname: importer.import_oid(native_oid)
            for refname, native_oid in needs_object.items()
        }
        known_by_native.update(importer.converted)
        native_map.update(
            {
                pygit_sha: native_oid
                for native_oid, pygit_sha in importer.converted.items()
            }
        )
        object_count = len(result.objects)

    followed = {**immediate, **fetched}
    for refname, sha in followed.items():
        repo.refs.set_tag(refname[len("refs/tags/") :], sha)
    return followed, object_count


def _controlled_source(
    destination: str,
    specs: Sequence[FetchRefspec],
) -> List[str]:
    sources: List[str] = []
    spec_list = list(specs)
    for spec in spec_list:
        if spec.negative:
            continue
        source = spec.source_for_destination(destination)
        if source is None or source_is_excluded(source, spec_list):
            continue
        sources.append(source)
    return sources


def _prune_refs(
    repo: Repository,
    remote: str,
    advertisement: Advertisement,
    specs: Sequence[FetchRefspec],
) -> List[str]:
    advertised = set(_advertised_sources(advertisement))
    pruned: List[str] = []

    for branch in list(repo.refs.list_remotes(remote)):
        destination = f"refs/remotes/{remote}/{branch}"
        sources = _controlled_source(destination, specs)
        if sources and not any(source in advertised for source in sources):
            repo.refs.delete_remote(remote, branch)
            pruned.append(destination)

    for tag in list(repo.refs.list_tags()):
        destination = f"refs/tags/{tag}"
        sources = _controlled_source(destination, specs)
        if sources and not any(source in advertised for source in sources):
            repo.refs.delete_tag(tag)
            pruned.append(destination)

    return pruned


def fetch_configured(
    repo: Repository,
    remote: str = "origin",
    *,
    prune: Optional[bool] = None,
    prune_tags: Optional[bool] = None,
    tags: Optional[bool] = None,
) -> Dict[str, object]:
    """Fetch one named remote using configured mapping/prune/tag policy."""
    url = fetch_url(repo, remote)
    client = SmartHttpClient(url)
    advertisement = client.discover()
    policy = resolve_fetch_policy(
        repo,
        remote,
        prune=prune,
        prune_tags=prune_tags,
        tags=tags,
    )
    configured = _parsed_fetch_refspecs(repo, remote)
    selection_specs, prune_specs = _selection_specs(configured, policy)

    pruned = _prune_refs(repo, remote, advertisement, prune_specs) if policy.prune else []

    native_map = repo._read_native_map(remote)
    known_by_native = {native: sha for sha, native in native_map.items()}
    explicit_sources, destinations = _select_explicit_sources(advertisement, selection_specs)

    imported, object_count = _fetch_import_sources(
        repo,
        client,
        advertisement,
        explicit_sources,
        native_map,
        known_by_native,
    )
    # _fetch_import_sources writes through an internal compatibility shim; make
    # the authoritative per-remote map explicit here.
    _write_native_map(repo, remote, native_map)
    _apply_destinations(repo, imported, destinations)

    if policy.tag_mode == "auto" and not policy.prune_tags:
        followed, tag_objects = _auto_follow_tags(
            repo,
            client,
            advertisement,
            native_map,
            known_by_native,
            imported.keys(),
        )
        if followed:
            imported.update(followed)
            _write_native_map(repo, remote, native_map)
        object_count += tag_objects

    default_ref = advertisement.symrefs.get("HEAD")
    default_branch = (
        default_ref[len("refs/heads/") :]
        if default_ref and default_ref.startswith("refs/heads/")
        else repo._infer_default_branch(advertisement.refs)
    )

    # Keep historical JSON metadata coherent, but never retarget the symbolic
    # remote HEAD during ordinary fetch. `remote set-head -a` owns that action.
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if settings is not None:
        settings["default_branch"] = default_branch
        repo._write_config(config)

    return {
        "remote": remote,
        "default_branch": default_branch,
        "refs": imported,
        "objects": object_count,
        "pruned": pruned,
        "tag_mode": policy.tag_mode,
    }
