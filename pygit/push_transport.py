"""Target-aware smart-HTTP push helpers.

Phase166 introduced branch-to-branch destination overrides. Phase167 extends the
same SHA-256-native exporter path to tags and deletion refspecs while keeping
``Repository.push(remote, force=...)`` untouched for compatibility.
"""

from __future__ import annotations

from typing import Dict, Optional

from .hooks import HookRunner
from .remote import NativeExporter, SmartHttpPushClient
from .repo import Repository


def _settings(repo: Repository, remote: str):
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    return settings


def _run_pre_push(repo: Repository, remote: str, url: str) -> None:
    code, out, err = HookRunner(repo.pygit_dir).run_hook("pre-push", [remote, url])
    if code != 0:
        raise RuntimeError(f"pre-push hook failed with exit code {code}:\n{err or out}")


def _internal_for_native(native_map, native_oid: str) -> Optional[str]:
    return next((internal for internal, native in native_map.items() if native == native_oid), None)


def push_ref(
    repo: Repository,
    remote: str,
    source_ref: str,
    target_ref: str,
    *,
    force: bool = False,
) -> Dict[str, object]:
    """Push one fully-qualified local ref to one fully-qualified remote ref."""
    if not source_ref.startswith("refs/") or not target_ref.startswith("refs/"):
        raise RuntimeError("push_ref requires fully-qualified refs")
    source_sha = repo.refs.resolve(source_ref)
    if not source_sha:
        raise KeyError(f"Unknown local ref: '{source_ref}'")

    settings = _settings(repo, remote)
    url = str(settings["url"])
    _run_pre_push(repo, remote, url)
    client = SmartHttpPushClient(url)
    advertisement = client.discover()
    old_native = advertisement.refs.get(target_ref, "0" * 40)
    native_map = repo._read_native_map(remote)
    old_internal = _internal_for_native(native_map, old_native) if old_native != "0" * 40 else None

    if target_ref.startswith("refs/heads/") and old_native != "0" * 40 and not force:
        if not old_internal or old_internal not in repo._ancestor_distances(source_sha):
            raise RuntimeError(
                "Push rejected: remote tip is not an ancestor of source branch; fetch first or use --force."
            )
    if target_ref.startswith("refs/tags/") and old_native != "0" * 40 and not force:
        raise RuntimeError("Push rejected: remote tag already exists; use --force to replace it.")

    have_shas = set(repo._ancestor_distances(old_internal)) if old_internal and target_ref.startswith("refs/heads/") else set()
    exporter = NativeExporter(repo.store, native_map, have_shas=have_shas)
    new_native = exporter.export_oid(source_sha)
    if old_native == new_native:
        return {
            "status": "up-to-date",
            "remote": remote,
            "source_ref": source_ref,
            "ref": target_ref,
            "sha": source_sha,
            "objects": 0,
        }

    result = client.push(target_ref, new_native, exporter.objects, advertisement=advertisement)
    native_map.update(exporter.converted)
    repo._write_native_map(native_map, remote)
    if target_ref.startswith("refs/heads/"):
        repo.refs.set_remote(remote, target_ref[len("refs/heads/") :], source_sha)
    return {
        "status": "pushed",
        "remote": remote,
        "source_ref": source_ref,
        "ref": target_ref,
        "sha": source_sha,
        "old_oid": result.old_oid,
        "new_oid": result.new_oid,
        "objects": result.objects_sent,
    }


def delete_remote_ref(repo: Repository, remote: str, target_ref: str) -> Dict[str, object]:
    """Delete one fully-qualified remote ref using a zero object ID update."""
    if not target_ref.startswith("refs/"):
        raise RuntimeError("delete_remote_ref requires a fully-qualified ref")
    settings = _settings(repo, remote)
    url = str(settings["url"])
    _run_pre_push(repo, remote, url)
    client = SmartHttpPushClient(url)
    advertisement = client.discover()
    old_native = advertisement.refs.get(target_ref, "0" * 40)
    if old_native == "0" * 40:
        return {"status": "up-to-date", "remote": remote, "ref": target_ref, "objects": 0}

    result = client.push(target_ref, "0" * 40, [], advertisement=advertisement)
    if target_ref.startswith("refs/heads/"):
        repo.refs.delete_remote(remote, target_ref[len("refs/heads/") :])
    return {
        "status": "deleted",
        "remote": remote,
        "ref": target_ref,
        "old_oid": result.old_oid,
        "new_oid": result.new_oid,
        "objects": result.objects_sent,
    }


def push_branch(
    repo: Repository,
    remote: str,
    source_branch: str,
    target_branch: str,
    *,
    force: bool = False,
) -> Dict[str, object]:
    """Compatibility wrapper for the Phase166 branch-aware transport."""
    result = push_ref(
        repo,
        remote,
        f"refs/heads/{source_branch}",
        f"refs/heads/{target_branch}",
        force=force,
    )
    result["source_branch"] = source_branch
    result["branch"] = target_branch
    return result
