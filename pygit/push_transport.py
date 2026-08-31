"""Target-aware smart-HTTP push helpers.

Phase166 introduced branch-to-branch destination overrides. Phase167 extends the
same SHA-256-native exporter path to tags and deletion refspecs while keeping
``Repository.push(remote, force=...)`` untouched for compatibility. Phase168
adds an opt-in atomic batch path without changing the historical single-ref
helpers. Phase169 adds optional force-with-lease checks to both paths. Phase171
adds opt-in receive-pack push-options without changing no-option call behavior.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .hooks import HookRunner
from .push_atomic import AtomicSmartHttpPushClient
from .push_defaults import PushSpec
from .push_lease import LeasePolicy, require_lease
from .push_options import (
    PushOptionAtomicSmartHttpPushClient,
    PushOptionSmartHttpPushClient,
    require_push_options_capability,
)
from .push_tracking import update_tracking_after_push
from .remote import NativeExporter, SmartHttpPushClient
from .repo import Repository


_ZERO_NATIVE_OID = "0" * 40


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
    lease: Optional[LeasePolicy] = None,
    push_options: Sequence[str] = (),
) -> Dict[str, object]:
    """Push one fully-qualified local ref to one fully-qualified remote ref."""
    if not source_ref.startswith("refs/") or not target_ref.startswith("refs/"):
        raise RuntimeError("push_ref requires fully-qualified refs")
    source_sha = repo.refs.resolve(source_ref)
    if not source_sha:
        raise KeyError(f"Unknown local ref: '{source_ref}'")

    option_values = tuple(push_options)
    settings = _settings(repo, remote)
    url = str(settings["url"])
    _run_pre_push(repo, remote, url)
    client = (
        PushOptionSmartHttpPushClient(url)
        if option_values
        else SmartHttpPushClient(url)
    )
    advertisement = client.discover()
    if option_values:
        require_push_options_capability(advertisement, option_values)
    old_native = advertisement.refs.get(target_ref, _ZERO_NATIVE_OID)
    native_map = repo._read_native_map(remote)
    old_internal = _internal_for_native(native_map, old_native) if old_native != _ZERO_NATIVE_OID else None

    lease_force = False
    if not force:
        lease_force = require_lease(
            lease,
            repo,
            remote,
            target_ref,
            old_native,
            native_map,
        )
    effective_force = bool(force or lease_force)

    if target_ref.startswith("refs/heads/") and old_native != _ZERO_NATIVE_OID and not effective_force:
        if not old_internal or old_internal not in repo._ancestor_distances(source_sha):
            raise RuntimeError(
                "Push rejected: remote tip is not an ancestor of source branch; fetch first or use --force."
            )
    if target_ref.startswith("refs/tags/") and old_native != _ZERO_NATIVE_OID and not effective_force:
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

    if option_values:
        result = client.push_with_options(
            target_ref,
            new_native,
            exporter.objects,
            option_values,
            advertisement=advertisement,
        )
    else:
        result = client.push(target_ref, new_native, exporter.objects, advertisement=advertisement)
    native_map.update(exporter.converted)
    repo._write_native_map(native_map, remote)
    update_tracking_after_push(repo, remote, target_ref, source_sha)
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


def delete_remote_ref(
    repo: Repository,
    remote: str,
    target_ref: str,
    *,
    force: bool = False,
    lease: Optional[LeasePolicy] = None,
    push_options: Sequence[str] = (),
) -> Dict[str, object]:
    """Delete one fully-qualified remote ref using a zero object ID update."""
    if not target_ref.startswith("refs/"):
        raise RuntimeError("delete_remote_ref requires a fully-qualified ref")
    option_values = tuple(push_options)
    settings = _settings(repo, remote)
    url = str(settings["url"])
    _run_pre_push(repo, remote, url)
    client = (
        PushOptionSmartHttpPushClient(url)
        if option_values
        else SmartHttpPushClient(url)
    )
    advertisement = client.discover()
    if option_values:
        require_push_options_capability(advertisement, option_values)
    old_native = advertisement.refs.get(target_ref, _ZERO_NATIVE_OID)
    native_map = repo._read_native_map(remote)
    if not force:
        require_lease(lease, repo, remote, target_ref, old_native, native_map)
    if old_native == _ZERO_NATIVE_OID:
        return {"status": "up-to-date", "remote": remote, "ref": target_ref, "objects": 0}

    if option_values:
        result = client.push_with_options(
            target_ref,
            _ZERO_NATIVE_OID,
            {},
            option_values,
            advertisement=advertisement,
        )
    else:
        result = client.push(target_ref, _ZERO_NATIVE_OID, [], advertisement=advertisement)
    update_tracking_after_push(repo, remote, target_ref, None)
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
    lease: Optional[LeasePolicy] = None,
    push_options: Sequence[str] = (),
) -> Dict[str, object]:
    """Compatibility wrapper for the Phase166 branch-aware transport."""
    kwargs = {"force": force, "lease": lease}
    if push_options:
        kwargs["push_options"] = push_options
    result = push_ref(
        repo,
        remote,
        f"refs/heads/{source_branch}",
        f"refs/heads/{target_branch}",
        **kwargs,
    )
    result["source_branch"] = source_branch
    result["branch"] = target_branch
    return result


def push_atomic_specs(
    repo: Repository,
    remote: str,
    specs: Sequence[PushSpec],
    *,
    force: bool = False,
    lease: Optional[LeasePolicy] = None,
    push_options: Sequence[str] = (),
) -> List[Tuple[PushSpec, Dict[str, object]]]:
    """Push *specs* as one receive-pack atomic transaction.

    All local/source, lease, push-option capability, and fast-forward checks are
    completed before the request is sent. Local native mappings and
    remote-tracking refs are updated only after the atomic receive-pack request
    succeeds.
    """
    if not specs:
        return []

    option_values = tuple(push_options)
    settings = _settings(repo, remote)
    url = str(settings["url"])
    _run_pre_push(repo, remote, url)
    client = (
        PushOptionAtomicSmartHttpPushClient(url)
        if option_values
        else AtomicSmartHttpPushClient(url)
    )
    advertisement = client.discover()
    if "atomic" not in advertisement.capabilities:
        raise RuntimeError("Remote does not support atomic pushes.")
    if option_values:
        require_push_options_capability(advertisement, option_values)

    native_map = repo._read_native_map(remote)
    prepared = []
    have_shas = set()

    for index, spec in enumerate(specs):
        target_ref = spec.target_ref
        old_native = advertisement.refs.get(target_ref, _ZERO_NATIVE_OID)
        effective_force = bool(force or spec.force)
        lease_force = False
        if not effective_force:
            lease_force = require_lease(
                lease,
                repo,
                remote,
                target_ref,
                old_native,
                native_map,
            )
        authorized_force = bool(effective_force or lease_force)

        if spec.delete:
            prepared.append(
                {
                    "index": index,
                    "spec": spec,
                    "target_ref": target_ref,
                    "old_native": old_native,
                    "source_sha": None,
                    "delete": True,
                }
            )
            continue

        source_ref = spec.source_ref
        source_sha = repo.refs.resolve(source_ref)
        if not source_sha:
            raise KeyError(f"Unknown local ref: '{source_ref}'")
        old_internal = (
            _internal_for_native(native_map, old_native)
            if old_native != _ZERO_NATIVE_OID
            else None
        )
        if target_ref.startswith("refs/heads/") and old_native != _ZERO_NATIVE_OID:
            if not authorized_force:
                if not old_internal or old_internal not in repo._ancestor_distances(source_sha):
                    raise RuntimeError(
                        "Push rejected: remote tip is not an ancestor of source branch; fetch first or use --force."
                    )
            if old_internal:
                have_shas.update(repo._ancestor_distances(old_internal))
        if target_ref.startswith("refs/tags/") and old_native != _ZERO_NATIVE_OID and not authorized_force:
            raise RuntimeError("Push rejected: remote tag already exists; use --force to replace it.")

        prepared.append(
            {
                "index": index,
                "spec": spec,
                "target_ref": target_ref,
                "old_native": old_native,
                "source_sha": source_sha,
                "delete": False,
            }
        )

    exporter = NativeExporter(repo.store, native_map, have_shas=have_shas)
    commands = []
    result_by_index: Dict[int, Tuple[PushSpec, Dict[str, object]]] = {}
    command_items = []

    for item in prepared:
        spec = item["spec"]
        old_native = str(item["old_native"])
        target_ref = str(item["target_ref"])
        if item["delete"]:
            new_native = _ZERO_NATIVE_OID
            if old_native == _ZERO_NATIVE_OID:
                result_by_index[int(item["index"])] = (
                    spec,
                    {
                        "status": "up-to-date",
                        "remote": remote,
                        "ref": target_ref,
                        "objects": 0,
                    },
                )
                continue
        else:
            source_sha = str(item["source_sha"])
            new_native = exporter.export_oid(source_sha)
            if old_native == new_native:
                result_by_index[int(item["index"])] = (
                    spec,
                    {
                        "status": "up-to-date",
                        "remote": remote,
                        "source_ref": spec.source_ref,
                        "ref": target_ref,
                        "sha": source_sha,
                        "objects": 0,
                    },
                )
                continue
        item["new_native"] = new_native
        commands.append((target_ref, new_native))
        command_items.append(item)

    batch_objects = 0
    if commands:
        if option_values:
            batch = client.push_many_with_options(
                commands,
                exporter.objects,
                option_values,
                advertisement=advertisement,
            )
        else:
            batch = client.push_many(commands, exporter.objects, advertisement=advertisement)
        batch_objects = batch.objects_sent

    # Do not mutate local state until the atomic server operation has succeeded.
    native_map.update(exporter.converted)
    if exporter.converted:
        repo._write_native_map(native_map, remote)

    for item in prepared:
        target_ref = str(item["target_ref"])
        if target_ref.startswith("refs/heads/"):
            update_tracking_after_push(
                repo,
                remote,
                target_ref,
                None if item["delete"] else str(item["source_sha"]),
            )

    first_command_index = int(command_items[0]["index"]) if command_items else -1
    for item in command_items:
        index = int(item["index"])
        spec = item["spec"]
        target_ref = str(item["target_ref"])
        old_native = str(item["old_native"])
        new_native = str(item["new_native"])
        objects = batch_objects if index == first_command_index else 0
        if item["delete"]:
            result = {
                "status": "deleted",
                "remote": remote,
                "ref": target_ref,
                "old_oid": old_native,
                "new_oid": new_native,
                "objects": objects,
            }
        else:
            result = {
                "status": "pushed",
                "remote": remote,
                "source_ref": spec.source_ref,
                "ref": target_ref,
                "sha": str(item["source_sha"]),
                "old_oid": old_native,
                "new_oid": new_native,
                "objects": objects,
            }
        result_by_index[index] = (spec, result)

    return [result_by_index[index] for index in range(len(specs))]
