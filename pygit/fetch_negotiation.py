"""Git-style fetch negotiation have-set controls.

Phase197 keeps pygit's protocol-v0 smart-HTTP transport while making the set of
local commits reported as ``have`` lines controllable.  The command-scoped
policy mirrors current Git's ``--negotiation-restrict`` (with the older
``--negotiation-tip`` spelling retained as an alias) and
``--negotiation-include`` semantics.
"""

from __future__ import annotations

import fnmatch
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set

from .objects import CommitObject, TagObject
from .remote import NativeExporter, SmartHttpClient
from .repo import Repository


_GLOB_CHARS = frozenset("*?[")


def _all_ref_values(repo: Repository) -> Dict[str, str]:
    """Return the local refs relevant to negotiation glob expansion."""
    result: Dict[str, str] = {}
    head = repo.refs.resolve_head()
    if head:
        result["HEAD"] = head

    for branch in repo.refs.list_branches():
        oid = repo.refs.get_branch(branch)
        if oid:
            result[f"refs/heads/{branch}"] = oid
    for tag in repo.refs.list_tags():
        oid = repo.refs.get_tag(tag)
        if oid:
            result[f"refs/tags/{tag}"] = oid
    for remote_ref in repo.refs.list_remotes():
        if remote_ref.endswith("/HEAD"):
            continue
        oid = repo.refs.resolve(f"refs/remotes/{remote_ref}")
        if oid:
            result[f"refs/remotes/{remote_ref}"] = oid

    stash = repo.refs.get_stash()
    if stash:
        result["refs/stash"] = stash
    return result


def _peel_commit(repo: Repository, oid: str) -> str:
    """Peel an annotated tag chain and require a commit target."""
    current = oid
    seen: Set[str] = set()
    for _ in range(32):
        if current in seen:
            raise RuntimeError("cycle while peeling negotiation tip")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, CommitObject):
            return current
        if isinstance(obj, TagObject):
            current = obj.target_sha
            continue
        raise RuntimeError("negotiation tip does not resolve to a commit")
    raise RuntimeError("negotiation tip tag chain is too deep")


def resolve_negotiation_tips(repo: Repository, expressions: Sequence[str]) -> List[str]:
    """Resolve exact revisions or full-ref globs to unique commit tips."""
    refs = _all_ref_values(repo)
    result: List[str] = []

    for expression in expressions:
        if not expression:
            raise ValueError("negotiation tip must be non-empty")
        matches: List[str] = []
        if any(char in expression for char in _GLOB_CHARS):
            matches = [
                oid
                for refname, oid in refs.items()
                if fnmatch.fnmatchcase(refname, expression)
            ]
            if not matches:
                raise RuntimeError(
                    f"negotiation tip pattern '{expression}' does not match any refs"
                )
        else:
            oid = repo.refs.resolve(expression)
            if oid is None:
                oid = repo.store.resolve_prefix(expression)
            if oid is None:
                raise RuntimeError(f"'{expression}' is not a valid negotiation tip")
            matches = [oid]

        for oid in matches:
            commit = _peel_commit(repo, oid)
            if commit not in result:
                result.append(commit)
    return result


def _shallow_boundaries(repo: Repository) -> Set[str]:
    path = repo.pygit_dir / "shallow"
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def reachable_commits(repo: Repository, tips: Iterable[str]) -> List[str]:
    """Walk commit ancestry from *tips*, respecting local shallow boundaries."""
    shallow = _shallow_boundaries(repo)
    pending = list(tips)
    seen: Set[str] = set()
    ordered: List[str] = []
    while pending:
        oid = pending.pop()
        if oid in seen:
            continue
        seen.add(oid)
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError("negotiation ancestry contains a non-commit object")
        ordered.append(oid)
        if oid not in shallow:
            pending.extend(reversed(obj.parents))
    return ordered


def _known_native_oids(repo: Repository) -> Dict[str, str]:
    """Combine per-remote SHA maps as an exporter acceleration cache."""
    result: Dict[str, str] = {}
    for remote in repo.list_remotes():
        result.update(repo._read_native_map(remote))
    return result


def _native_commit_oids(repo: Repository, commits: Iterable[str]) -> Set[str]:
    known = _known_native_oids(repo)
    exporter = NativeExporter(
        repo.store,
        known_oids=known,
        have_shas=set(known),
    )
    return {exporter.export_oid(oid) for oid in commits}


def plan_restricted_haves(repo: Repository, expressions: Sequence[str]) -> Set[str]:
    """Return native SHA-1 haves reachable from the requested restriction tips."""
    tips = resolve_negotiation_tips(repo, expressions)
    return _native_commit_oids(repo, reachable_commits(repo, tips))


def plan_included_haves(repo: Repository, expressions: Sequence[str]) -> Set[str]:
    """Return native SHA-1 OIDs for tips that must always be reported."""
    tips = resolve_negotiation_tips(repo, expressions)
    return _native_commit_oids(repo, tips)


@contextmanager
def negotiation_transport(
    repo: Repository,
    *,
    restrict: Sequence[str] = (),
    include: Sequence[str] = (),
) -> Iterator[None]:
    """Temporarily rewrite ``SmartHttpClient.fetch`` have selection.

    Restriction replaces the caller's broad have set with commits reachable only
    from the selected tips.  Inclusion then adds the exact selected tip commits.
    The original transport method is restored even when a fetch raises.
    """
    restricted: Optional[Set[str]] = (
        plan_restricted_haves(repo, restrict) if restrict else None
    )
    included = plan_included_haves(repo, include) if include else set()
    original = SmartHttpClient.fetch

    def fetch_with_negotiation(self, haves=None, advertisement=None):
        planned = set(haves or []) if restricted is None else set(restricted)
        planned.update(included)
        return original(self, haves=planned, advertisement=advertisement)

    SmartHttpClient.fetch = fetch_with_negotiation
    try:
        yield
    finally:
        SmartHttpClient.fetch = original
