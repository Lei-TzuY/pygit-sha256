"""Bandwidth-saving protocol-v2 initial shallow clone.

Historical ``Repository.clone(depth=...)`` downloaded the complete native graph,
converted it, and only then wrote a logical ``.pygit/shallow`` boundary. Phase204
adds a real initial transport for the CLI: the server receives ``deepen N`` and
may omit history behind the returned shallow boundary.

The stable shallow importer stores original native commit parent identities, so
those omitted parents can be fetched later without rewriting already-visible
local SHA-256 commit ids.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .fetch_importer import StableShallowNativeImporter
from .fetch_shallow import _apply_shallow_response
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
) -> dict[str, str]:
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


def clone_shallow_repository(
    url: str,
    path: Optional[str],
    *,
    depth: int,
    branch_name: Optional[str],
    single_branch: bool,
) -> Repository:
    """Create a repository from a genuinely truncated protocol-v2 pack."""
    if depth <= 0:
        raise ValueError("shallow clone depth must be a positive integer")
    if path is None:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        path = name[:-4] if name.endswith(".git") else name

    destination = Path(path).resolve()
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise RuntimeError(f"Destination path is not empty: {destination}")

    repo = Repository.init(str(destination))
    repo.add_remote("origin", url)

    client = SmartHttpV2FetchClient(url)
    advertisement = client.discover_refs()
    if advertisement is None:
        raise RuntimeError("shallow clone requires protocol version 2")

    default_branch = _default_branch(repo, advertisement)
    target_branch = branch_name or default_branch or "main"
    selected_refs = _selected_branch_refs(
        advertisement,
        target_branch=target_branch,
        single_branch=single_branch,
    )
    selected_advertisement = Advertisement(
        refs=selected_refs,
        capabilities=set(advertisement.capabilities),
        symrefs=dict(advertisement.symrefs),
    )

    result = client.fetch(
        haves=[],
        advertisement=selected_advertisement,
        deepen=depth,
    )
    if result is None:
        raise RuntimeError("shallow clone requires protocol version 2")

    importer = StableShallowNativeImporter(repo.store, result.objects)
    imported = {
        refname: importer.import_oid(native_oid)
        for refname, native_oid in selected_refs.items()
    }
    known_by_native = dict(importer.converted)
    native_map = {
        local_sha: native_oid
        for native_oid, local_sha in importer.converted.items()
    }
    repo._write_native_map(native_map, "origin")

    for refname, local_sha in imported.items():
        branch = refname[len("refs/heads/") :]
        repo.refs.set_remote("origin", branch, local_sha)

    _apply_shallow_response(repo, result, known_by_native)

    config = repo._read_config()
    settings = config.setdefault("remotes", {}).setdefault("origin", {"url": url})
    settings["url"] = url
    settings["default_branch"] = default_branch
    repo._write_config(config)

    # Phase202 shallow fetch controls are intentionally protocol-v2 strict.
    # Persist the protocol preference for the repository created by this path
    # so later `fetch --deepen` / `fetch --unshallow` continues the same model.
    repo.config_set("protocol", "version", "2")

    target_sha = imported[f"refs/heads/{target_branch}"]
    repo.refs.set_branch(target_branch, target_sha, message=f"clone: from {url}")
    repo.refs.set_head_symbolic(target_branch, message=f"clone: from {url}")
    repo._replace_worktree_from_commit(target_sha)
    return repo
