"""Explicit tag clone orchestration with a detached SHA-256-native HEAD.

Git accepts ``clone --branch <name>`` when *name* identifies a tag instead of a
branch.  The historical pygit clone paths interpret that option only as
``refs/heads/<name>``.  This module adds the narrow protocol-v2 tag case without
changing branch precedence or the legacy protocol-v0 clone implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .clone_unborn import _clone_destination, _rollback_empty_clone_destination
from .fetch_importer import TagPreservingNativeImporter
from .objects import CommitObject
from .protocol_v2_fetch import SmartHttpV2FetchClient
from .remote import Advertisement
from .repo import Repository


@dataclass(frozen=True)
class TagCloneResult:
    """One successfully cloned tag with HEAD detached at its peeled commit."""

    repo: Repository
    tag: str
    commit_oid: str


def _default_branch(advertisement: Advertisement) -> Optional[str]:
    target = advertisement.symrefs.get("HEAD")
    prefix = "refs/heads/"
    if target and target.startswith(prefix) and len(target) > len(prefix):
        return target[len(prefix) :]
    return None


def _selected_tag_advertisement(
    advertisement: Advertisement,
    tag_ref: str,
) -> Advertisement:
    """Return the minimum advertisement required for one selected tag."""

    refs = {tag_ref: advertisement.refs[tag_ref]}
    peeled_name = f"{tag_ref}^{{}}"
    if peeled_name in advertisement.refs:
        refs[peeled_name] = advertisement.refs[peeled_name]
    return Advertisement(
        refs=refs,
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )


def _clone_roots(
    advertisement: Advertisement,
    *,
    tag_ref: str,
    single_branch: bool,
) -> dict[str, str]:
    if single_branch:
        return {tag_ref: advertisement.refs[tag_ref]}
    return {
        name: oid
        for name, oid in advertisement.refs.items()
        if name.startswith("refs/heads/")
        or (name.startswith("refs/tags/") and not name.endswith("^{}"))
    }


def _configure_tag_clone_remote(
    repo: Repository,
    advertisement: Advertisement,
    *,
    url: str,
    tag_ref: str,
    single_branch: bool,
) -> None:
    """Persist the observable Git config/ref shape of ``clone -b <tag>``."""

    default_branch = _default_branch(advertisement)
    config = repo._read_config()
    settings = config.setdefault("remotes", {}).setdefault("origin", {})
    settings["url"] = url
    settings["default_branch"] = default_branch
    repo._write_config(config)

    repo.config_set("remote", "origin.url", url)
    if single_branch:
        repo.config_set("remote", "origin.fetch", f"+{tag_ref}:{tag_ref}")
        repo.refs.delete_remote_head("origin")
    else:
        repo.config_set(
            "remote",
            "origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        )
        if default_branch and repo.refs.get_remote("origin", default_branch) is not None:
            repo.refs.set_remote_head("origin", default_branch)
        else:
            repo.refs.delete_remote_head("origin")


def try_clone_explicit_tag_remote(
    url: str,
    path: Optional[str],
    *,
    branch_name: Optional[str],
    single_branch: bool,
    server_options: Sequence[str] = (),
    checkout: bool = True,
) -> Optional[TagCloneResult]:
    """Clone an explicit tag request, or return ``None`` for the normal paths.

    Branches intentionally win over tags with the same short name, matching
    native Git's branch-or-tag resolution.  Protocol-v0 is represented by
    ``None`` and remains on the historical ``Repository.clone`` path.
    """

    if branch_name is None:
        return None

    client = SmartHttpV2FetchClient(
        url,
        server_options=tuple(server_options),
    )
    advertisement = client.discover_refs()
    if advertisement is None:
        return None

    branch_ref = f"refs/heads/{branch_name}"
    if branch_ref in advertisement.refs:
        return None

    tag_ref = f"refs/tags/{branch_name}"
    tag_native_oid = advertisement.refs.get(tag_ref)
    if tag_native_oid is None:
        return None

    selected = (
        _selected_tag_advertisement(advertisement, tag_ref)
        if single_branch
        else advertisement
    )
    fetched = client.fetch(haves=(), advertisement=selected)
    if fetched is None:
        raise RuntimeError("tag clone requires protocol version 2 after v2 discovery")
    if fetched.shallow or fetched.unshallow:
        raise RuntimeError("ordinary tag clone unexpectedly changed shallow state")

    destination = _clone_destination(url, path)
    destination_existed = destination.exists()
    repo: Optional[Repository] = None
    try:
        repo = Repository.init(str(destination))
        repo.add_remote("origin", url)

        importer = TagPreservingNativeImporter(repo.store, fetched.objects)
        roots = _clone_roots(
            advertisement,
            tag_ref=tag_ref,
            single_branch=single_branch,
        )
        imported = {
            refname: importer.import_oid(native_oid)
            for refname, native_oid in roots.items()
        }

        for refname, local_oid in imported.items():
            if refname.startswith("refs/heads/"):
                repo.refs.set_remote(
                    "origin",
                    refname[len("refs/heads/") :],
                    local_oid,
                )
            elif refname.startswith("refs/tags/"):
                repo.refs.set_tag(refname[len("refs/tags/") :], local_oid)

        peeled_native_oid = advertisement.refs.get(
            f"{tag_ref}^{{}}",
            tag_native_oid,
        )
        peeled_local_oid = importer.converted.get(peeled_native_oid)
        if peeled_local_oid is None:
            peeled_local_oid = importer.import_oid(peeled_native_oid)
        peeled = repo.store.read(peeled_local_oid)
        if not isinstance(peeled, CommitObject):
            raise RuntimeError("clone --branch tag must peel to a commit")

        native_map = {
            local_oid: native_oid
            for native_oid, local_oid in importer.converted.items()
        }
        repo._write_native_map(native_map, "origin")
        _configure_tag_clone_remote(
            repo,
            advertisement,
            url=url,
            tag_ref=tag_ref,
            single_branch=single_branch,
        )

        repo.refs.set_head_detached(
            peeled_local_oid,
            message=f"clone: from {url}",
        )
        if checkout:
            repo._replace_worktree_from_commit(peeled_local_oid)
    except Exception:
        _rollback_empty_clone_destination(
            destination,
            existed=destination_existed,
        )
        raise

    return TagCloneResult(
        repo=repo,
        tag=branch_name,
        commit_oid=peeled_local_oid,
    )
