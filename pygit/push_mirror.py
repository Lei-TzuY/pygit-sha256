"""Plan Git-style ``push --mirror`` updates across the full ``refs/`` namespace."""

from __future__ import annotations

from typing import Optional, Tuple

from .plumbing import list_refs
from .push_defaults import PushSpec
from .remote import SmartHttpPushClient
from .repo import Repository


def _parse_bool(value: Optional[str], *, key: str) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"invalid boolean value for {key}: '{value}'")


def configured_remote_mirror(repo: Repository, remote: str) -> bool:
    """Return whether ``remote.<name>.mirror`` enables mirror pushes."""
    return _parse_bool(
        repo.config_get("remote", f"{remote}.mirror"),
        key=f"remote.{remote}.mirror",
    )


def _settings(repo: Repository, remote: str):
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    return settings


def _split_ref(refname: str) -> Tuple[str, str]:
    if not refname.startswith("refs/"):
        raise RuntimeError(f"mirror ref is outside refs/: '{refname}'")
    relative = refname[len("refs/") :]
    namespace, separator, name = relative.partition("/")
    if not separator or not namespace or not name:
        raise RuntimeError(f"mirror ref has no namespace/name pair: '{refname}'")
    return namespace, name


def _local_order(refname: str) -> Tuple[int, str]:
    # ``push_ref`` updates refs/remotes/<remote>/<branch> after a successful
    # heads update. Snapshot-like mirror behavior therefore sends existing local
    # remote-tracking refs first, before any branch transport can refresh them.
    if refname.startswith("refs/remotes/"):
        return 0, refname
    if refname.startswith("refs/heads/"):
        return 2, refname
    return 1, refname


def mirror_specs(repo: Repository, remote: str) -> Tuple[PushSpec, ...]:
    """Return force-update/create/delete specs that mirror every ``refs/*`` ref.

    Local refs are enumerated from both loose and packed storage. Remote-only
    advertised refs are represented as deletions. ``HEAD`` and any other
    pseudo-ref outside ``refs/`` are intentionally ignored.
    """
    local_refnames = {refname for _oid, refname in list_refs(repo)}
    local_specs = []
    for refname in sorted(local_refnames, key=_local_order):
        namespace, name = _split_ref(refname)
        local_specs.append(
            PushSpec(name, name, force=True, namespace=namespace)
        )

    settings = _settings(repo, remote)
    advertisement = SmartHttpPushClient(str(settings["url"])).discover()
    deletions = []
    for refname in sorted(advertisement.refs):
        if not refname.startswith("refs/") or refname in local_refnames:
            continue
        namespace, name = _split_ref(refname)
        deletions.append(
            PushSpec("", name, force=True, namespace=namespace, delete=True)
        )

    return tuple(local_specs + deletions)
