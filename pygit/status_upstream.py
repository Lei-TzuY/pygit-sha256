"""Resolve status upstream tracking from Git-style branch configuration.

Phase 163 teaches modern status rendering to prefer the current branch's
``branch.<name>.remote`` / ``branch.<name>.merge`` tracking configuration over
the repository's older implicit ``origin/<current>`` heuristic.

The repository stores ordinary user config in ``.pygit/config`` through
:class:`pygit.config.GitConfig`. Because the existing config command represents
``branch.main.remote`` as section ``branch`` plus key ``main.remote``, this
module intentionally reads the same shape instead of introducing a second
subsection parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .repo import Repository


@dataclass(frozen=True)
class UpstreamSpec:
    """One configured upstream target for status presentation."""

    display: str
    revision: str
    remote: str
    branch: str


def _merge_branch(value: str) -> Optional[str]:
    """Return the branch name represented by a branch.<name>.merge value."""
    token = value.strip()
    prefix = "refs/heads/"
    if token.startswith(prefix) and len(token) > len(prefix):
        return token[len(prefix) :]
    # Pygit's config command historically accepts arbitrary scalar values.  A
    # simple branch name is harmless and useful, while other ref namespaces are
    # deliberately left unsupported until refspec mapping grows beyond heads.
    if token and "/" not in token:
        return token
    return None


def configured_upstream(repo: Repository, branch: str) -> Tuple[Optional[UpstreamSpec], bool]:
    """Return ``(spec, configured)`` for *branch*.

    ``configured`` distinguishes a genuinely absent tracking configuration from
    a partial/unsupported one.  Once either branch tracking key is present, the
    modern status layer does not silently fall back to ``origin/<branch>``.
    """
    remote = repo.config_get("branch", f"{branch}.remote")
    merge = repo.config_get("branch", f"{branch}.merge")
    if remote is None and merge is None:
        return None, False
    if not remote or not merge:
        return None, True

    target_branch = _merge_branch(merge)
    if target_branch is None:
        return None, True

    remote = remote.strip()
    if not remote:
        return None, True
    if remote == ".":
        return (
            UpstreamSpec(
                display=target_branch,
                revision=target_branch,
                remote=remote,
                branch=target_branch,
            ),
            True,
        )

    display = f"{remote}/{target_branch}"
    return (
        UpstreamSpec(
            display=display,
            revision=display,
            remote=remote,
            branch=target_branch,
        ),
        True,
    )


def _target_exists(repo: Repository, spec: UpstreamSpec) -> bool:
    if spec.remote == ".":
        return repo.refs.get_branch(spec.branch) is not None
    return repo.refs.get_remote(spec.remote, spec.branch) is not None


def resolve_status_upstream(
    repo: Repository,
    legacy: object = None,
) -> Optional[Dict[str, object]]:
    """Resolve the current branch's status upstream metadata.

    Configured tracking wins.  A configured-but-missing tracking ref is retained
    as ``gone`` so short/long status can report it and porcelain v2 can emit
    ``branch.upstream`` without a ``branch.ab`` line, matching native Git.

    When no branch tracking keys exist at all, Phase 163 deliberately preserves
    the pre-existing modern-status fallback supplied by :meth:`Repository.status`
    so older callers/tests that only materialize ``origin/<current>`` do not
    regress.  New configured repositories take the Git-style path.
    """
    branch = repo.refs.current_branch()
    if not branch:
        return None

    spec, configured = configured_upstream(repo, branch)
    if not configured:
        return dict(legacy) if isinstance(legacy, dict) else None
    if spec is None:
        return None

    if not _target_exists(repo, spec):
        return {
            "upstream": spec.display,
            "gone": True,
            "remote": spec.remote,
            "branch": spec.branch,
        }

    try:
        ahead, behind = repo.ahead_behind("HEAD", spec.revision)
    except Exception:
        return {
            "upstream": spec.display,
            "gone": True,
            "remote": spec.remote,
            "branch": spec.branch,
        }

    return {
        "upstream": spec.display,
        "ahead": int(ahead),
        "behind": int(behind),
        "gone": False,
        "remote": spec.remote,
        "branch": spec.branch,
    }
