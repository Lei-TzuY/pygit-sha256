"""Plan CAS-safe remote-tracking publications for packfile-URI fetches.

Phase328 turns a protocol-v2 advertisement into the ``expected_roots`` and
``PackfileUriRefPublication`` inputs consumed by the exact-green Phase327
repository fetch adapter.  The planner is deliberately read-only and limited to
remote branches: tag publication has different object-type semantics (annotated
versus lightweight tags) and remains outside this boundary.

Remote advertisement identities stay genuine 40-hex SHA-1 object ids.  Existing
tracking refs are read only as full local 64-hex SHA-256 ids and become the CAS
``old_local_oid`` values for Phase323 publication; a missing ref uses the local
``ZERO_SHA`` creation sentinel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional

from .protocol_v2_packfile_uri_refs import PackfileUriRefPublication
from .ref_query import check_ref_format
from .refs import ZERO_SHA
from .remote import Advertisement
from .repo import Repository

_HEX = frozenset("0123456789abcdef")
_BRANCH_PREFIX = "refs/heads/"


@dataclass(frozen=True)
class PackfileUriRemoteTrackingPlan:
    """Read-only publication plan for one remote's tracking branches."""

    expected_roots: Dict[str, bytes]
    publications: Dict[str, PackfileUriRefPublication]
    default_branch: Optional[str]


def _native_oid(value: object, *, refname: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(
            f"advertised branch {refname!r} must contain a full remote-native SHA-1"
        )
    lowered = value.lower()
    if any(ch not in _HEX for ch in lowered):
        raise ValueError(
            f"advertised branch {refname!r} must contain a hexadecimal remote-native SHA-1"
        )
    return lowered


def _local_oid(value: str, *, refname: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(
            f"existing tracking ref {refname!r} must contain a full local SHA-256"
        )
    lowered = value.lower()
    if any(ch not in _HEX for ch in lowered):
        raise ValueError(
            f"existing tracking ref {refname!r} must contain a hexadecimal local SHA-256"
        )
    return lowered


def _validate_remote_name(remote: object) -> str:
    if not isinstance(remote, str) or not remote:
        raise ValueError("packfile-URI remote name must be a non-empty string")
    # Validate the name in the exact ref namespace that this planner will publish.
    # A synthetic terminal component keeps nested-but-valid remote names legal.
    check_ref_format(f"refs/remotes/{remote}/__phase328_probe__")
    return remote


def _normalize_requested_branches(
    advertised: Mapping[str, str],
    branches: Optional[Iterable[str]],
) -> tuple[str, ...]:
    available = tuple(sorted(name for name in advertised if name.startswith(_BRANCH_PREFIX)))
    if branches is None:
        selected = available
    else:
        if isinstance(branches, (str, bytes)):
            raise TypeError("packfile-URI branch selection must be an iterable of full ref names")
        seen = set()
        ordered = []
        for branch in branches:
            if not isinstance(branch, str) or not branch.startswith(_BRANCH_PREFIX):
                raise ValueError(
                    "packfile-URI branch selection requires full refs/heads/... names"
                )
            normalized = check_ref_format(branch)
            if normalized in seen:
                raise ValueError(f"duplicate packfile-URI branch selection: {normalized}")
            seen.add(normalized)
            if normalized not in advertised:
                raise ValueError(
                    f"requested packfile-URI branch was not advertised: {normalized}"
                )
            ordered.append(normalized)
        selected = tuple(ordered)

    if not selected:
        raise ValueError("packfile-URI remote-tracking plan requires at least one branch")
    return selected


def plan_packfile_uri_remote_tracking_publication(
    repo: Repository,
    advertisement: Advertisement,
    remote: str = "origin",
    *,
    branches: Optional[Iterable[str]] = None,
) -> PackfileUriRemoteTrackingPlan:
    """Build Phase327 root/publication inputs for remote-tracking branches.

    Every selected ``refs/heads/<branch>`` is mapped to
    ``refs/remotes/<remote>/<branch>``.  The advertised native SHA-1 becomes a
    required ``commit`` root.  The local tracking ref's current SHA-256 becomes
    the exact CAS old value; a missing tracking ref uses :data:`ZERO_SHA`.

    No refs, config, native-map, HEAD, or object-store state is modified.  The
    returned plan can therefore be discarded safely if transport negotiation or
    any later verification boundary fails.
    """

    if not isinstance(repo, Repository):
        raise TypeError("packfile-URI tracking planner requires a Repository")
    if not isinstance(advertisement, Advertisement):
        raise TypeError("packfile-URI tracking planner requires an Advertisement")
    remote = _validate_remote_name(remote)

    selected = _normalize_requested_branches(advertisement.refs, branches)
    expected_roots: Dict[str, bytes] = {}
    publications: Dict[str, PackfileUriRefPublication] = {}
    selected_short = set()

    for remote_ref in selected:
        short = remote_ref[len(_BRANCH_PREFIX) :]
        local_ref = check_ref_format(f"refs/remotes/{remote}/{short}")
        native = _native_oid(advertisement.refs[remote_ref], refname=remote_ref)

        current = repo.refs.get_remote(remote, short)
        old_local = ZERO_SHA if current is None else _local_oid(current, refname=local_ref)

        expected_roots[native] = b"commit"
        publications[local_ref] = PackfileUriRefPublication(native, old_local)
        selected_short.add(short)

    default_branch: Optional[str] = None
    head_target = advertisement.symrefs.get("HEAD")
    if isinstance(head_target, str) and head_target.startswith(_BRANCH_PREFIX):
        candidate = head_target[len(_BRANCH_PREFIX) :]
        if candidate in selected_short and head_target in advertisement.refs:
            # Validate the target too; this also prevents carrying malformed
            # symbolic-ref metadata into a later remote-HEAD publication phase.
            check_ref_format(head_target)
            default_branch = candidate

    return PackfileUriRemoteTrackingPlan(
        expected_roots=expected_roots,
        publications=publications,
        default_branch=default_branch,
    )
