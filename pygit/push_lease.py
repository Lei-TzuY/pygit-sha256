"""Git-style ``push --force-with-lease`` parsing and expectation checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from .remote import NativeExporter
from .repo import Repository


_ZERO_NATIVE_OID = "0" * 40
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class LeaseRequest:
    """One command-line lease request.

    ``target_ref=None`` means the request protects every ref selected for push.
    ``explicit_expect`` distinguishes ``--force-with-lease=<ref>`` from
    ``--force-with-lease=<ref>:<expect>``.  An empty explicit expectation means
    that the remote ref must not exist.
    """

    target_ref: Optional[str]
    expect: Optional[str] = None
    explicit_expect: bool = False


@dataclass(frozen=True)
class LeasePolicy:
    requests: Tuple[LeaseRequest, ...] = ()
    force_if_includes: bool = False

    @property
    def active(self) -> bool:
        return bool(self.requests)

    def with_force_if_includes(self, enabled: bool) -> "LeasePolicy":
        """Return the same lease requests with the ancillary includes guard."""
        return LeasePolicy(self.requests, force_if_includes=bool(enabled))

    def request_for(self, target_ref: str) -> Optional[LeaseRequest]:
        """Return the last applicable request for *target_ref*.

        Command-line order matters: a later ref-specific request may refine a
        preceding global lease, while a later global request may replace the
        effective expectation again.  ``--no-force-with-lease`` is handled by
        the argument extractor by clearing all preceding requests.
        """
        selected: Optional[LeaseRequest] = None
        for request in self.requests:
            if request.target_ref is None or request.target_ref == target_ref:
                selected = request
        return selected

    def expected_native(
        self,
        repo: Repository,
        remote: str,
        target_ref: str,
        native_map: Dict[str, str],
    ) -> Optional[str]:
        """Return the native SHA-1 value required by the applicable lease.

        ``None`` means no lease applies to this target.  The all-zero native OID
        means the lease expects the remote ref not to exist.
        """
        request = self.request_for(target_ref)
        if request is None:
            return None

        if request.explicit_expect:
            expect = request.expect or ""
            if expect == "":
                return _ZERO_NATIVE_OID
            lowered = expect.lower()
            if len(lowered) == 40 and all(char in _HEX for char in lowered):
                # Interop convenience: an explicitly supplied remote-native
                # SHA-1 can be compared directly at the smart-HTTP boundary.
                return lowered
            internal = repo._resolve_revision(expect)
            mapped = native_map.get(internal)
            if mapped:
                return mapped
            return NativeExporter(repo.store, native_map).export_oid(internal)

        if target_ref.startswith("refs/heads/"):
            branch = target_ref[len("refs/heads/") :]
            internal = repo.refs.get_remote(remote, branch)
        else:
            # Git has remote-tracking branches, not a parallel tracking
            # namespace for arbitrary refs/tags.  Without an explicit expected
            # value an unknown tracking value behaves like an expected absence.
            internal = None

        if not internal:
            return _ZERO_NATIVE_OID
        mapped = native_map.get(internal)
        if mapped:
            return mapped
        return NativeExporter(repo.store, native_map).export_oid(internal)


def _target_ref(value: str) -> str:
    text = value.strip()
    if not text:
        raise RuntimeError("--force-with-lease requires a non-empty ref name")
    if text.startswith("refs/"):
        return text
    return f"refs/heads/{text}"


def extract_force_with_lease(argv: Sequence[str]) -> Tuple[Tuple[str, ...], LeasePolicy]:
    """Remove lease options from argv while preserving Git's ordering semantics.

    Git's optional ``=<ref>[:<expect>]`` value is attached to the long option;
    a following positional token remains the repository/refspec.  This helper
    therefore avoids argparse ``nargs='?'`` accidentally consuming ``origin``.
    """
    cleaned = []
    requests = []
    for token in argv:
        if token == "--force-with-lease":
            requests.append(LeaseRequest(None))
            continue
        if token == "--no-force-with-lease":
            requests.clear()
            continue
        if token.startswith("--force-with-lease="):
            detail = token.split("=", 1)[1]
            if not detail:
                raise RuntimeError("--force-with-lease requires a ref after '='")
            if ":" in detail:
                refname, expect = detail.split(":", 1)
                requests.append(
                    LeaseRequest(
                        _target_ref(refname),
                        expect=expect,
                        explicit_expect=True,
                    )
                )
            else:
                requests.append(LeaseRequest(_target_ref(detail)))
            continue
        cleaned.append(token)
    return tuple(cleaned), LeasePolicy(tuple(requests))


def require_lease(
    policy: Optional[LeasePolicy],
    repo: Repository,
    remote: str,
    target_ref: str,
    actual_native: str,
    native_map: Dict[str, str],
) -> bool:
    """Validate the applicable lease and return whether it authorizes force.

    A matching lease authorizes the selected ref to bypass the ordinary
    fast-forward/tag-replacement restriction.  A stale lease rejects before any
    local tracking state is changed.  Phase170 optionally adds the reflog-based
    ``--force-if-includes`` proof after an implicit lease value matches.
    """
    if policy is None or not policy.active:
        return False
    expected = policy.expected_native(repo, remote, target_ref, native_map)
    if expected is None:
        return False
    if actual_native != expected:
        raise RuntimeError(f"Push rejected: stale info for '{target_ref}'.")
    if policy.force_if_includes:
        from .push_includes import require_force_if_includes

        require_force_if_includes(
            True,
            policy,
            repo,
            remote,
            target_ref,
        )
    return True
