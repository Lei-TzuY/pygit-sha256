"""Post-push remote-tracking updates derived from configured fetch refspecs.

Git does not create or advance a local remote-tracking ref merely because a push
succeeded.  The pushed remote ref must be mapped back into the local repository
by ``remote.<name>.fetch``.  This distinction matters for an empty
``--single-branch`` clone: native Git intentionally leaves the fetch refspec
unset, so the first push creates the remote branch without fabricating
``refs/remotes/<remote>/<branch>`` locally.
"""

from __future__ import annotations

from typing import Optional


def _match_refspec(source_ref: str, spec: str) -> Optional[str]:
    """Return the destination ref selected by one supported fetch refspec.

    Pygit's current remote configuration stores one ordinary positive fetch
    refspec.  Support Git's exact and single-wildcard forms, including the
    leading force marker.  Malformed/negative refspecs fail closed by returning
    ``None`` instead of turning a successful remote push into a local error.
    """

    token = str(spec).strip()
    if token.startswith("+"):
        token = token[1:]
    if not token or token.startswith("^") or token.count(":") != 1:
        return None

    source, destination = token.split(":", 1)
    if not source or not destination:
        return None

    source_stars = source.count("*")
    destination_stars = destination.count("*")
    if source_stars != destination_stars or source_stars not in {0, 1}:
        return None

    if source_stars == 0:
        return destination if source_ref == source else None

    source_prefix, source_suffix = source.split("*", 1)
    if not source_ref.startswith(source_prefix) or not source_ref.endswith(source_suffix):
        return None
    end = len(source_ref) - len(source_suffix) if source_suffix else len(source_ref)
    wildcard = source_ref[len(source_prefix) : end]
    if not wildcard:
        return None
    return destination.replace("*", wildcard, 1)


def tracking_branch_for_push(repo, remote: str, target_ref: str) -> Optional[str]:
    """Return the local ``refs/remotes/<remote>/...`` branch mapped by fetch.

    Modern Git-style remotes obey ``remote.<name>.fetch`` exactly.  Historical
    pygit repositories created only through ``Repository.add_remote()`` predate
    that config surface; preserve their established same-name tracking behavior
    until they acquire an explicit Git-style ``remote.<name>.url`` entry.

    Only mappings inside the named remote-tracking namespace are accepted.  This
    keeps post-push bookkeeping within the RefStore API and refuses surprising
    custom destinations rather than mutating unrelated local refs.
    """

    if not target_ref.startswith("refs/heads/"):
        return None
    source_branch = target_ref[len("refs/heads/") :]
    if not source_branch:
        return None

    fetch_spec = repo.config_get("remote", f"{remote}.fetch")
    if not fetch_spec:
        # Compatibility boundary: old repositories may have only the legacy
        # ``remotes`` JSON entry.  Phase331+ clone orchestration writes the
        # Git-style URL key, so an absent fetch refspec there is intentional and
        # must not synthesize a tracking ref.
        if repo.config_get("remote", f"{remote}.url") is None:
            return source_branch
        return None
    destination = _match_refspec(target_ref, fetch_spec)
    if destination is None:
        return None

    prefix = f"refs/remotes/{remote}/"
    if not destination.startswith(prefix):
        return None
    branch = destination[len(prefix) :]
    if not branch or branch.startswith("/") or branch.endswith("/") or ".." in branch:
        return None
    return branch


def update_tracking_after_push(
    repo,
    remote: str,
    target_ref: str,
    local_oid: Optional[str],
) -> Optional[str]:
    """Apply one successful push to its fetch-mapped remote-tracking ref.

    ``local_oid=None`` represents a successful remote deletion.  The returned
    value is the relative remote-tracking branch that was updated, or ``None``
    when no configured fetch refspec maps the pushed remote ref.
    """

    branch = tracking_branch_for_push(repo, remote, target_ref)
    if branch is None:
        return None
    if local_oid is None:
        repo.refs.delete_remote(remote, branch)
    else:
        repo.refs.set_remote(remote, branch, local_oid)
    return branch


def install_repository_push_tracking_support(repository_cls) -> None:
    """Wrap the historical ``Repository.push`` tracking side effect safely.

    The legacy method predates Git-style fetch refspec configuration and always
    updates ``origin/<current>``.  Preserve its transport/export behavior while
    restoring the pre-push tracking value whenever that same-name ref is not the
    configured fetch destination, then publish the actual mapped destination.
    """

    original = getattr(repository_cls, "push")
    if getattr(original, "_pygit_fetch_refspec_tracking", False):
        return

    def push(self, remote: str = "origin", force: bool = False):
        branch = self.refs.current_branch()
        target_ref = f"refs/heads/{branch}" if branch else ""
        mapped = tracking_branch_for_push(self, remote, target_ref) if branch else None
        same_before = self.refs.get_remote(remote, branch) if branch else None
        mapped_before = (
            self.refs.get_remote(remote, mapped)
            if mapped is not None and mapped != branch
            else None
        )

        result = original(self, remote=remote, force=force)
        if not branch or result.get("status") != "pushed":
            return result

        # The historical implementation has just written remote/<branch>.
        # Undo only that write when the configured fetch mapping differs.
        if mapped != branch:
            if same_before is None:
                self.refs.delete_remote(remote, branch)
            else:
                self.refs.set_remote(remote, branch, same_before)

        if mapped is not None and mapped != branch:
            oid = str(result.get("sha") or self.refs.get_branch(branch) or "")
            if not oid:
                # A successful branch push must have a local source oid.  Restore
                # the mapped ref defensively if an unexpected caller violates it.
                if mapped_before is None:
                    self.refs.delete_remote(remote, mapped)
                else:
                    self.refs.set_remote(remote, mapped, mapped_before)
                raise RuntimeError("successful push did not expose a local branch oid")
            self.refs.set_remote(remote, mapped, oid)
        return result

    push._pygit_fetch_refspec_tracking = True
    repository_cls.push = push
