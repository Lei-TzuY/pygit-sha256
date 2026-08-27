"""Target-aware branch push transport for Phase 166.

The historical :meth:`Repository.push` API always pushes the current branch to
an identically named remote branch. Keep that API untouched for compatibility;
this helper reuses the same smart-HTTP/native-export machinery when a resolved
push refspec needs a different source or destination branch.
"""

from __future__ import annotations

from typing import Dict, Optional

from .hooks import HookRunner
from .remote import NativeExporter, SmartHttpPushClient
from .repo import Repository


def push_branch(
    repo: Repository,
    remote: str,
    source_branch: str,
    target_branch: str,
    *,
    force: bool = False,
) -> Dict[str, object]:
    """Push one local branch to one named remote branch."""

    source_sha = repo.refs.get_branch(source_branch)
    if not source_sha:
        raise KeyError(f"Unknown local branch: '{source_branch}'")
    if not target_branch:
        raise RuntimeError("remote destination branch must not be empty")

    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")

    hook_runner = HookRunner(repo.pygit_dir)
    code, out, err = hook_runner.run_hook(
        "pre-push", [remote, str(settings["url"])]
    )
    if code != 0:
        raise RuntimeError(
            f"pre-push hook failed with exit code {code}:\n{err or out}"
        )

    client = SmartHttpPushClient(str(settings["url"]))
    advertisement = client.discover()
    ref_name = f"refs/heads/{target_branch}"
    old_native = advertisement.refs.get(ref_name, "0" * 40)
    native_map = repo._read_native_map(remote)

    old_internal: Optional[str] = None
    if old_native != "0" * 40:
        old_internal = next(
            (
                internal
                for internal, native in native_map.items()
                if native == old_native
            ),
            None,
        )

    if old_native != "0" * 40 and not force:
        if (
            not old_internal
            or old_internal not in repo._ancestor_distances(source_sha)
        ):
            raise RuntimeError(
                "Push rejected: remote tip is not an ancestor of source branch; "
                "fetch first or use --force."
            )

    have_shas = (
        set(repo._ancestor_distances(old_internal))
        if old_internal
        else set()
    )
    exporter = NativeExporter(repo.store, native_map, have_shas=have_shas)
    new_native = exporter.export_oid(source_sha)
    if old_native == new_native:
        repo.refs.set_remote(remote, target_branch, source_sha)
        return {
            "status": "up-to-date",
            "remote": remote,
            "source_branch": source_branch,
            "branch": target_branch,
            "sha": source_sha,
            "objects": 0,
        }

    result = client.push(
        ref_name,
        new_native,
        exporter.objects,
        advertisement=advertisement,
    )
    native_map.update(exporter.converted)
    repo._write_native_map(native_map, remote)
    repo.refs.set_remote(remote, target_branch, source_sha)
    return {
        "status": "pushed",
        "remote": remote,
        "source_branch": source_branch,
        "branch": target_branch,
        "sha": source_sha,
        "old_oid": result.old_oid,
        "new_oid": result.new_oid,
        "objects": result.objects_sent,
    }
