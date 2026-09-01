"""Protocol-v2 empty-clone orchestration for explicit unborn ``HEAD`` metadata.

Phase315 preserves the remote-native ``unborn HEAD`` record and Phase317 can
materialize that reference state locally without fabricating an object id.  This
module composes those two boundaries into clone-time behavior while deliberately
leaving every non-empty and protocol-v0 clone on the established clone paths.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .clone_origin import DEFAULT_CLONE_REMOTE, validate_clone_remote_name
from .protocol_v2_unborn import (
    ProtocolV2LsRefsResult,
    SmartHttpV2UnbornQueryClient,
)
from .repo import Repository
from .unborn_init import EmptyRemoteInitializationError, initialize_empty_remote_head


@dataclass(frozen=True)
class EmptyRemoteCloneResult:
    """A successfully initialized clone of one explicitly empty remote."""

    repo: Repository
    branch: str


def _clone_destination(url: str, path: Optional[str]) -> Path:
    """Resolve the destination using the historical ``Repository.clone`` rule."""

    if path is None:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        path = name[:-4] if name.endswith(".git") else name
    destination = Path(path).resolve()
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise RuntimeError(f"Destination path is not empty: {destination}")
    return destination


def _explicit_unborn_branch(result: ProtocolV2LsRefsResult) -> Optional[str]:
    """Return the advertised unborn branch, or ``None`` for an ordinary result.

    Once an explicit unborn record is present, fail closed on any conflicting
    remote identity instead of treating the response as a normal clone fallback.
    The strict Phase315 parser already constrains unborn records to ``HEAD``;
    these checks protect the orchestration boundary from hand-built result
    objects and future callers.
    """

    if not result.unborn:
        return None
    if result.unborn != frozenset({"HEAD"}):
        raise EmptyRemoteInitializationError(
            "empty clone requires exactly one explicit unborn HEAD record"
        )

    advertisement = result.advertisement
    if advertisement.refs:
        raise EmptyRemoteInitializationError(
            "explicit unborn HEAD cannot be combined with concrete remote refs"
        )
    if set(advertisement.symrefs) != {"HEAD"}:
        raise EmptyRemoteInitializationError(
            "explicit unborn HEAD requires exactly one HEAD symref target"
        )
    target = advertisement.symrefs.get("HEAD")
    if not target or not target.startswith("refs/heads/"):
        raise EmptyRemoteInitializationError(
            "unborn HEAD symref-target must name refs/heads/<branch>"
        )
    branch = target[len("refs/heads/") :]
    if not branch:
        raise EmptyRemoteInitializationError(
            "unborn HEAD symref-target must name a non-empty branch"
        )
    return branch


def _record_historical_remote_default(
    repo: Repository,
    *,
    url: str,
    branch: str,
    remote: str,
) -> None:
    """Keep the pre-Git-config remote metadata used by ``Repository.fetch``."""

    config = repo._read_config()
    settings = config.setdefault("remotes", {}).setdefault(remote, {})
    settings["url"] = url
    settings["default_branch"] = branch
    repo._write_config(config)


def _configure_empty_clone_metadata(
    repo: Repository,
    *,
    url: str,
    branch: str,
    remote: str,
    single_branch: bool,
    depth: Optional[int],
    filter_spec: Optional[str],
) -> None:
    """Persist the Git-visible metadata of a successful empty clone.

    Native Git keeps branch upstream metadata even though the corresponding
    remote-tracking ref does not yet exist.  For ``--single-branch`` it omits the
    fetch refspec entirely because there is no concrete selected branch ref.
    """

    repo.config_set("remote", f"{remote}.url", url)
    if not single_branch:
        repo.config_set(
            "remote",
            f"{remote}.fetch",
            f"+refs/heads/*:refs/remotes/{remote}/*",
        )
    repo.refs.delete_remote_head(remote)

    repo.config_set("branch", f"{branch}.remote", remote)
    repo.config_set("branch", f"{branch}.merge", f"refs/heads/{branch}")

    # The mature pygit shallow/partial clone paths persist protocol v2 because
    # later deepen/materialization operations depend on it.  An empty clone has
    # no shallow boundary or promisor object yet, but it should retain the same
    # transport preference/configuration selected by the clone mode.
    if depth is not None or filter_spec is not None:
        repo.config_set("protocol", "version", "2")
    if filter_spec is not None:
        repo.config_set("extensions", "partialClone", remote)
        repo.config_set("remote", f"{remote}.promisor", "true")
        repo.config_set("remote", f"{remote}.partialCloneFilter", filter_spec)


def _rollback_empty_clone_destination(destination: Path, *, existed: bool) -> None:
    """Undo local initialization after a post-init failure."""

    if existed:
        pygit_dir = destination / ".pygit"
        if pygit_dir.exists():
            shutil.rmtree(pygit_dir)
    elif destination.exists():
        shutil.rmtree(destination)


def try_clone_explicit_unborn_remote(
    url: str,
    path: Optional[str],
    *,
    branch_name: Optional[str],
    single_branch: bool,
    server_options: Sequence[str] = (),
    depth: Optional[int] = None,
    filter_spec: Optional[str] = None,
    remote_name: str = DEFAULT_CLONE_REMOTE,
) -> Optional[EmptyRemoteCloneResult]:
    """Clone an explicitly unborn protocol-v2 remote, or return ``None``.

    A ``None`` result is the compatibility signal for both protocol-v0 fallback
    and ordinary non-empty protocol-v2 advertisements.  Those cases must remain
    on the established clone/import pipelines.  Only an explicit Phase315
    ``unborn HEAD`` record can enter the metadata-only empty-clone path.
    """

    remote_name = validate_clone_remote_name(remote_name)
    destination = _clone_destination(url, path)
    destination_existed = destination.exists()

    client = SmartHttpV2UnbornQueryClient(
        url,
        server_options=tuple(server_options),
    )
    result = client.discover_refs_with_unborn()
    if result is None:
        return None

    unborn_branch = _explicit_unborn_branch(result)
    if unborn_branch is None:
        return None

    # Native Git treats --branch as a request for a concrete remote ref.  An
    # unborn symref target is not such a ref, even when the spelling matches.
    if branch_name is not None:
        raise RuntimeError(
            f"Remote branch {branch_name} not found in upstream {remote_name}"
        )

    repo: Optional[Repository] = None
    try:
        repo = Repository.init(str(destination))
        repo.add_remote(remote_name, url)
        _record_historical_remote_default(
            repo,
            url=url,
            branch=unborn_branch,
            remote=remote_name,
        )
        branch = initialize_empty_remote_head(repo, result)
        _configure_empty_clone_metadata(
            repo,
            url=url,
            branch=branch,
            remote=remote_name,
            single_branch=single_branch,
            depth=depth,
            filter_spec=filter_spec,
        )
    except Exception:
        _rollback_empty_clone_destination(
            destination,
            existed=destination_existed,
        )
        raise

    return EmptyRemoteCloneResult(repo=repo, branch=branch)
