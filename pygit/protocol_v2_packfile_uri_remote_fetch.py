"""Named-remote repository fetch through protocol-v2 packfile URIs.

Phase329 composes the exact-green Phase328 remote-tracking planner with the
Phase327 Smart HTTP repository adapter.  The public entry point resolves a
configured pygit remote, performs protocol-v2 ref discovery, derives exact
remote-tracking CAS publications, and only then enters the verified packfile-URI
repository transaction.

The wrapper deliberately does not persist pygit's legacy native-map metadata.
Remote advertisement and pack identities remain genuine SHA-1 values; local refs
remain content-derived SHA-256 values returned by the existing importer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .protocol_v2_packfile_uri_repository import (
    SmartHttpV2PackfileUriRepositoryResult,
    fetch_packfile_uris_into_repository,
)
from .protocol_v2_packfile_uri_tracking import (
    PackfileUriRemoteTrackingPlan,
    plan_packfile_uri_remote_tracking_publication,
)
from .protocol_v2_packfile_uris import SmartHttpV2PackfileUriClient
from .remote import Advertisement
from .repo import Repository


@dataclass(frozen=True)
class NamedRemotePackfileUriFetchResult:
    """Successful named-remote packfile-URI fetch and publication."""

    remote: str
    url: str
    advertisement: Advertisement
    plan: PackfileUriRemoteTrackingPlan
    repository: SmartHttpV2PackfileUriRepositoryResult


def _configured_remote_url(repo: Repository, remote: str) -> str:
    if not isinstance(remote, str) or not remote:
        raise ValueError("packfile-URI named remote must be a non-empty string")

    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    url = settings.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"Remote '{remote}' does not have a valid URL")
    return url


def fetch_named_remote_with_packfile_uris(
    repo: Repository,
    remote: str = "origin",
    *,
    protocols: Sequence[str] = ("https",),
    branches: Optional[Iterable[str]] = None,
    haves: Optional[Iterable[str]] = None,
    shallow: Iterable[str] = (),
    deepen: Optional[int] = None,
    deepen_relative: bool = False,
    timeout: int = 30,
    server_options: Sequence[str] = (),
    message: Optional[str] = None,
    external_timeout: Optional[int] = None,
    max_pack_bytes: int = 256 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
    max_packs: int = 64,
    opener=None,
) -> Optional[NamedRemotePackfileUriFetchResult]:
    """Fetch configured remote branches through the verified packfile-URI path.

    ``None`` is returned only when the initial Smart HTTP discovery proves that
    the server did not speak protocol v2.  No tracking-ref plan or repository
    transaction is entered in that case.

    For a v2 server, discovery happens before repository mutation.  Phase328 then
    derives exact expected-old local SHA-256 CAS values for the selected remote
    branches.  Phase327 reuses the same advertisement while performing the
    terminating packfile-URI fetch, and Phase326 keeps ref publication last.

    ``haves`` are accepted for explicit callers but are intentionally not derived
    from pygit's legacy native-map automatically.  The staging layer remains
    authoritative and fails closed if a server omits graph objects required for
    content-derived SHA-256 import.
    """

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI named fetch requires a Repository")

    url = _configured_remote_url(repo, remote)
    client = SmartHttpV2PackfileUriClient(
        url,
        timeout=timeout,
        server_options=server_options,
    )

    advertisement = client.discover_refs()
    if advertisement is None:
        return None
    if not isinstance(advertisement, Advertisement):
        raise TypeError("packfile-URI ref discovery returned an unexpected result type")

    plan = plan_packfile_uri_remote_tracking_publication(
        repo,
        advertisement,
        remote=remote,
        branches=branches,
    )
    publication_message = message or f"fetch: {remote} via verified protocol-v2 packfile-uri"

    repository_result = fetch_packfile_uris_into_repository(
        repo,
        client,
        protocols,
        plan.expected_roots,
        plan.publications,
        haves=haves,
        advertisement=advertisement,
        shallow=shallow,
        deepen=deepen,
        deepen_relative=deepen_relative,
        message=publication_message,
        external_timeout=external_timeout,
        max_pack_bytes=max_pack_bytes,
        max_total_bytes=max_total_bytes,
        max_packs=max_packs,
        opener=opener,
    )
    if repository_result is None:
        # Discovery already proved v2.  A later downgrade/fallback is not a safe
        # successful named-remote fetch, even though no ref mutation occurred.
        raise RuntimeError("Remote stopped speaking protocol v2 during packfile-URI fetch")

    return NamedRemotePackfileUriFetchResult(
        remote=remote,
        url=url,
        advertisement=advertisement,
        plan=plan,
        repository=repository_result,
    )
