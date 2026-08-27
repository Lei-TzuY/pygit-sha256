"""Git-style ``push --follow-tags`` selection for annotated tags."""

from __future__ import annotations

from typing import Optional, Sequence, Set, Tuple

from .objects import CommitObject, TagObject
from .plumbing import ancestor_distances
from .push_defaults import PushPlan, PushSpec, configured_push_refspecs
from .remote import SmartHttpPushClient
from .repo import Repository


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def configured_follow_tags(repo: Repository) -> bool:
    """Return the local ``push.followTags`` boolean, defaulting to false."""
    value = repo.config_get("push", "followTags")
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise RuntimeError(f"invalid boolean value for push.followTags: '{value}'")


def resolve_follow_tags(repo: Repository, cli_override: Optional[bool]) -> bool:
    """Resolve explicit ``--[no-]follow-tags`` over ``push.followTags``."""
    if cli_override is not None:
        return cli_override
    return configured_follow_tags(repo)


def _settings(repo: Repository, remote: str):
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    return settings


def _peel_commit(repo: Repository, oid: str) -> Optional[str]:
    """Peel an annotated-tag chain and return its commit target, if any."""
    current = oid
    seen: Set[str] = set()
    while True:
        if current in seen:
            raise RuntimeError(f"Tag cycle while peeling {oid}")
        seen.add(current)
        obj = repo.store.read(current)
        if isinstance(obj, CommitObject):
            return current
        if isinstance(obj, TagObject):
            current = obj.target_sha
            continue
        return None


def _pushed_commit_roots(repo: Repository, plan: PushPlan) -> Tuple[str, ...]:
    roots = []
    seen = set()
    for spec in plan.specs:
        if spec.delete:
            continue
        oid = repo.refs.resolve(spec.source_ref)
        if not oid:
            continue
        commit = _peel_commit(repo, oid)
        if commit is not None and commit not in seen:
            seen.add(commit)
            roots.append(commit)
    return tuple(roots)


def _raw_refspecs(
    repo: Repository,
    remote: str,
    plan: PushPlan,
    explicit_refspecs: Sequence[str],
) -> Tuple[str, ...]:
    if explicit_refspecs:
        return tuple(explicit_refspecs)
    if plan.mode == "remote.push":
        return configured_push_refspecs(repo, remote)
    return ()


def _negative_tag_patterns(values: Sequence[str]) -> Tuple[str, ...]:
    patterns = []
    for value in values:
        raw = value.strip()
        if not raw.startswith("^"):
            continue
        body = raw[1:]
        if body.startswith("refs/tags/"):
            pattern = body[len("refs/tags/") :]
            if pattern:
                patterns.append(pattern)
    return tuple(patterns)


def _matches(name: str, pattern: str) -> bool:
    if "*" not in pattern:
        return name == pattern
    prefix, suffix = pattern.split("*", 1)
    return name.startswith(prefix) and name.endswith(suffix)


def follow_tag_specs(
    repo: Repository,
    remote: str,
    plan: PushPlan,
    explicit_refspecs: Sequence[str] = (),
) -> Tuple[PushSpec, ...]:
    """Return missing reachable annotated tags implied by ``--follow-tags``.

    Only annotated tag refs are considered.  Their recursively peeled target
    must be a commit reachable from at least one non-deletion source ref already
    selected by *plan*.  Tags already present on the remote, already selected by
    the push plan, or excluded by a negative tag refspec are skipped.
    """
    roots = _pushed_commit_roots(repo, plan)
    if not roots:
        return ()

    reachable: Set[str] = set()
    for root in roots:
        reachable.update(ancestor_distances(repo, root))

    raw_refspecs = _raw_refspecs(repo, remote, plan, explicit_refspecs)
    negative_tags = _negative_tag_patterns(raw_refspecs)
    selected_targets = {spec.target_ref for spec in plan.specs}

    url = str(_settings(repo, remote)["url"])
    advertisement = SmartHttpPushClient(url).discover()
    remote_refs = set(advertisement.refs)

    result = []
    for name in repo.refs.list_tags():
        target_ref = f"refs/tags/{name}"
        if target_ref in selected_targets or target_ref in remote_refs:
            continue
        if any(_matches(name, pattern) for pattern in negative_tags):
            continue

        oid = repo.refs.get_tag(name)
        if not oid:
            continue
        obj = repo.store.read(oid)
        if not isinstance(obj, TagObject):
            # Lightweight tags are deliberately not followed.
            continue
        commit = _peel_commit(repo, oid)
        if commit is None or commit not in reachable:
            continue
        result.append(PushSpec(name, name, namespace="tags"))

    return tuple(result)
