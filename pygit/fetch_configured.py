"""Configured fetch transport for clone-generated remote-tracking mappings.

Phase181 makes the ``remote.<name>.fetch`` written by clone operational rather
than decorative.  The selector intentionally focuses on branch-source
selection (including exact, wildcard, and negative source patterns) while
preserving pygit's existing automatic tag import behavior.
"""

from __future__ import annotations

import fnmatch
from typing import Dict, List

from .config import GitConfig
from .remote import NativeImporter, SmartHttpClient
from .remote_urls import fetch_url
from .repo import Repository


def configured_fetch_refspecs(repo: Repository, remote: str) -> List[str]:
    """Return ordered configured fetch refspecs, or Git's normal branch mapping."""
    values = GitConfig(repo.pygit_dir).get_all("remote", f"{remote}.fetch")
    return values or [f"+refs/heads/*:refs/remotes/{remote}/*"]


def _source_pattern(raw: str) -> tuple[bool, str]:
    token = raw.strip()
    negative = token.startswith("^")
    if negative:
        token = token[1:]
    elif token.startswith("+"):
        token = token[1:]
    source = token.split(":", 1)[0]
    if not source:
        raise ValueError(f"invalid fetch refspec: {raw!r}")
    if not source.startswith("refs/"):
        source = f"refs/heads/{source}"
    if source.count("*") > 1:
        raise ValueError(f"unsupported fetch refspec pattern: {raw!r}")
    return negative, source


def _matches(refname: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(refname, pattern) if "*" in pattern else refname == pattern


def select_fetch_import_refs(
    repo: Repository,
    remote: str,
    native_refs: Dict[str, str],
) -> Dict[str, str]:
    """Filter advertised refs through ``remote.<name>.fetch`` branch sources.

    The advertisement's pseudo-ref ``HEAD`` is metadata, not a tracking ref, so
    it is never a transfer target here.  Tags retain pygit's historical import
    behavior.  Positive branch patterns establish the fetched branch domain;
    negative refspecs subtract from it.
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


def fetch_configured(repo: Repository, remote: str = "origin") -> Dict[str, object]:
    """Fetch one named remote while honoring its configured branch refspecs."""
    url = fetch_url(repo, remote)
    client = SmartHttpClient(url)
    advertisement = client.discover()
    native_map = repo._read_native_map(remote)
    known_by_native = {native: sha for sha, native in native_map.items()}
    native_refs = repo._advertised_import_refs(advertisement.refs)
    native_refs = select_fetch_import_refs(repo, remote, native_refs)

    if native_refs and all(native_oid in known_by_native for native_oid in native_refs.values()):
        imported = {
            ref_name: known_by_native[native_oid]
            for ref_name, native_oid in native_refs.items()
        }
        object_count = 0
    elif not native_refs:
        imported = {}
        object_count = 0
    else:
        result = client.fetch(
            haves=native_map.values(),
            advertisement=advertisement,
        )
        importer = NativeImporter(repo.store, result.objects, known=known_by_native)
        imported = {
            ref_name: importer.import_oid(native_oid)
            for ref_name, native_oid in native_refs.items()
        }
        native_map.update(
            {
                pygit_sha: native_oid
                for native_oid, pygit_sha in importer.converted.items()
            }
        )
        repo._write_native_map(native_map, remote)
        object_count = len(result.objects)

    for ref_name, sha in imported.items():
        if ref_name.startswith("refs/heads/"):
            repo.refs.set_remote(remote, ref_name[len("refs/heads/") :], sha)
        elif ref_name.startswith("refs/tags/"):
            repo.refs.set_tag(ref_name[len("refs/tags/") :], sha)

    default_ref = advertisement.symrefs.get("HEAD")
    default_branch = (
        default_ref[len("refs/heads/") :]
        if default_ref and default_ref.startswith("refs/heads/")
        else repo._infer_default_branch(advertisement.refs)
    )

    # Keep the historical JSON metadata coherent because older Repository APIs
    # still consult it.  Deliberately do not update refs/remotes/<remote>/HEAD:
    # native `git fetch` leaves that symbolic ref unchanged until set-head -a.
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
    }
