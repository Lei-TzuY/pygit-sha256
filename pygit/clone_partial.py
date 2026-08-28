"""Protocol-v2 partial clone built on the Phase212/213 promisor model.

A filtered clone fetches the complete selected commit/tree graph while allowing
blob filters to omit file contents. Before the normal initial checkout,
Phase214 batches only the still-promised blobs reachable from the selected HEAD
worktree. Historical blobs remain promised and continue to materialize lazily
when later commands actually need them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence, Set

from .fetch_importer import PromisorFilteredNativeImporter
from .fetch_partial import _filtered_v2_fetch, _validate_filter_spec
from .objects import CommitObject, TreeObject
from .promisor import promised_kind
from .promisor_materialize import materialize_promised_objects
from .protocol_v2_fetch import SmartHttpV2FetchClient
from .remote import Advertisement
from .repo import Repository


def _default_branch(repo: Repository, advertisement: Advertisement) -> Optional[str]:
    default_ref = advertisement.symrefs.get("HEAD")
    if default_ref and default_ref.startswith("refs/heads/"):
        return default_ref[len("refs/heads/") :]
    return repo._infer_default_branch(advertisement.refs)


def _selected_branch_refs(
    advertisement: Advertisement,
    *,
    target_branch: str,
    single_branch: bool,
) -> Dict[str, str]:
    target_ref = f"refs/heads/{target_branch}"
    target_oid = advertisement.refs.get(target_ref)
    if target_oid is None:
        raise RuntimeError(f"Remote did not provide branch '{target_branch}'.")
    if single_branch:
        return {target_ref: target_oid}
    return {
        name: oid
        for name, oid in advertisement.refs.items()
        if name.startswith("refs/heads/")
    }


def _record_importer_state(
    importer: PromisorFilteredNativeImporter,
    known_by_native: Dict[str, str],
    native_map: Dict[str, str],
) -> None:
    known_by_native.update(importer.converted)
    native_map.update(
        {
            local_oid: native_oid
            for native_oid, local_oid in importer.converted.items()
        }
    )


def _eligible_auto_follow_tags(
    advertisement: Advertisement,
    known_by_native: Dict[str, str],
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for refname, native_oid in advertisement.refs.items():
        if not refname.startswith("refs/tags/") or refname.endswith("^{}"):
            continue
        peeled = advertisement.refs.get(f"{refname}^{{}}", native_oid)
        if peeled in known_by_native:
            result[refname] = native_oid
    return result


def _auto_follow_tags(
    repo: Repository,
    client: SmartHttpV2FetchClient,
    advertisement: Advertisement,
    initial_result,
    *,
    filter_spec: str,
    known_by_native: Dict[str, str],
    native_map: Dict[str, str],
) -> None:
    """Install tags whose peeled targets are already in the selected history."""
    eligible = _eligible_auto_follow_tags(advertisement, known_by_native)
    if not eligible:
        return

    imported_tags: Dict[str, str] = {}
    missing: Dict[str, str] = {}
    for refname, native_oid in eligible.items():
        local_oid = known_by_native.get(native_oid)
        if local_oid is not None:
            imported_tags[refname] = local_oid
        else:
            missing[refname] = native_oid

    if missing:
        selected = Advertisement(
            refs=dict(missing),
            capabilities=set(advertisement.capabilities),
            symrefs={},
        )
        commit_haves = sorted(
            native_oid
            for native_oid, native in initial_result.objects.items()
            if native.type_name == "commit" and native_oid in known_by_native
        )
        tag_result = _filtered_v2_fetch(
            client,
            haves=commit_haves,
            advertisement=selected,
            filter_spec=filter_spec,
        )
        if tag_result.shallow or tag_result.unshallow:
            raise RuntimeError(
                "partial-clone tag auto-follow unexpectedly changed shallow state"
            )
        importer = PromisorFilteredNativeImporter(
            repo.store,
            tag_result.objects,
            known=known_by_native,
            remote="origin",
            filter_spec=filter_spec,
        )
        for refname, native_oid in missing.items():
            imported_tags[refname] = importer.import_oid(native_oid)
        _record_importer_state(importer, known_by_native, native_map)

    for refname, local_oid in imported_tags.items():
        repo.refs.set_tag(refname[len("refs/tags/") :], local_oid)


def _collect_checkout_promises(repo: Repository, commit_sha: str) -> Set[str]:
    """Return promised native blobs required by one commit's worktree snapshot."""
    commit = repo.store.read(commit_sha)
    if not isinstance(commit, CommitObject):
        raise RuntimeError("partial clone target is not a commit")

    promised: Set[str] = set()
    pending = [commit.tree]
    seen: Set[str] = set()
    while pending:
        tree_sha = pending.pop()
        if tree_sha in seen:
            continue
        seen.add(tree_sha)
        tree = repo.store.read(tree_sha)
        if not isinstance(tree, TreeObject):
            raise RuntimeError("partial clone commit references a non-tree object")
        for entry in tree.entries:
            if entry.is_dir:
                # Filtered packs must contain all required trees, so this is a
                # network-free resolution even when the tree stores native refs.
                pending.append(entry.sha)
                continue
            if entry.is_resolved:
                continue
            if entry.native_oid and promised_kind(repo.pygit_dir, entry.native_oid):
                promised.add(entry.native_oid)
    return promised


def clone_partial_repository(
    url: str,
    path: Optional[str],
    *,
    filter_spec: str,
    branch_name: Optional[str],
    single_branch: bool,
    server_options: Sequence[str] = (),
    checkout: bool = True,
) -> Repository:
    """Create a partial clone, optionally leaving its worktree unpopulated."""
    filter_spec = _validate_filter_spec(filter_spec)
    if path is None:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        path = name[:-4] if name.endswith(".git") else name

    destination = Path(path).resolve()
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise RuntimeError(f"Destination path is not empty: {destination}")

    repo = Repository.init(str(destination))
    repo.add_remote("origin", url)

    client = (
        SmartHttpV2FetchClient(url, server_options=server_options)
        if server_options
        else SmartHttpV2FetchClient(url)
    )
    advertisement = client.discover_refs()
    if advertisement is None:
        raise RuntimeError("partial clone requires protocol version 2")

    default_branch = _default_branch(repo, advertisement)
    target_branch = branch_name or default_branch or "main"
    selected_refs = _selected_branch_refs(
        advertisement,
        target_branch=target_branch,
        single_branch=single_branch,
    )
    selected_advertisement = Advertisement(
        refs=dict(selected_refs),
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )

    result = _filtered_v2_fetch(
        client,
        haves=(),
        advertisement=selected_advertisement,
        filter_spec=filter_spec,
    )
    if result.shallow or result.unshallow:
        raise RuntimeError("partial clone from a shallow source is not yet supported")

    importer = PromisorFilteredNativeImporter(
        repo.store,
        result.objects,
        remote="origin",
        filter_spec=filter_spec,
    )
    imported = {
        refname: importer.import_oid(native_oid)
        for refname, native_oid in selected_refs.items()
    }
    known_by_native = dict(importer.converted)
    native_map = {
        local_oid: native_oid
        for native_oid, local_oid in importer.converted.items()
    }

    for refname, local_oid in imported.items():
        repo.refs.set_remote("origin", refname[len("refs/heads/") :], local_oid)

    _auto_follow_tags(
        repo,
        client,
        advertisement,
        result,
        filter_spec=filter_spec,
        known_by_native=known_by_native,
        native_map=native_map,
    )
    repo._write_native_map(native_map, "origin")

    config = repo._read_config()
    settings = config.setdefault("remotes", {}).setdefault("origin", {"url": url})
    settings["url"] = url
    settings["default_branch"] = default_branch
    repo._write_config(config)

    repo.config_set("protocol", "version", "2")
    repo.config_set("extensions", "partialClone", "origin")
    repo.config_set("remote", "origin.promisor", "true")
    repo.config_set("remote", "origin.partialCloneFilter", filter_spec)

    target_sha = imported[f"refs/heads/{target_branch}"]
    repo.refs.set_branch(target_branch, target_sha, message=f"clone: from {url}")
    repo.refs.set_head_symbolic(target_branch, message=f"clone: from {url}")

    if checkout:
        # Resolve current-worktree blobs in one request instead of triggering one
        # Phase213 HTTP round-trip per file. Historical blobs remain promised.
        checkout_promises = _collect_checkout_promises(repo, target_sha)
        if checkout_promises:
            materialize_promised_objects(repo.pygit_dir, sorted(checkout_promises))
        repo._replace_worktree_from_commit(target_sha)
    return repo
