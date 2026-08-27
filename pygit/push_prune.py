"""Plan Git-style ``push --prune`` deletions for supported ref namespaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple

from .push_defaults import PushPlan, PushSpec, configured_push_refspecs
from .remote import SmartHttpPushClient
from .repo import Repository


@dataclass(frozen=True)
class _PatternMapping:
    namespace: str
    source: str
    target: str


def _settings(repo: Repository, remote: str):
    config = repo._read_config()
    settings = config.get("remotes", {}).get(remote)
    if not settings:
        raise KeyError(f"Unknown remote: '{remote}'")
    return settings


def _parts(value: str) -> Tuple[str, str]:
    if value.startswith("refs/heads/"):
        return "heads", value[len("refs/heads/") :]
    if value.startswith("refs/tags/"):
        return "tags", value[len("refs/tags/") :]
    raise RuntimeError(f"--prune only supports refs/heads/* and refs/tags/* mappings: '{value}'")


def _mapping(text: str) -> _PatternMapping | None:
    raw = text.strip()
    if not raw or raw.startswith("^"):
        return None
    if raw.startswith("+"):
        raw = raw[1:]
    if raw in {":", "+:"}:
        return _PatternMapping("heads", "*", "*")
    if ":" not in raw:
        return None
    source, target = raw.split(":", 1)
    if source.count("*") != 1 or target.count("*") != 1:
        return None
    source_ns, source_pattern = _parts(source)
    target_ns, target_pattern = _parts(target)
    if source_ns != target_ns:
        return None
    return _PatternMapping(source_ns, source_pattern, target_pattern)


def _negative_patterns(values: Iterable[str]) -> Tuple[Tuple[str, str], ...]:
    result = []
    for value in values:
        raw = value.strip()
        if not raw.startswith("^"):
            continue
        body = raw[1:]
        if ":" in body:
            continue
        namespace, pattern = _parts(body)
        result.append((namespace, pattern))
    return tuple(result)


def _matches(name: str, pattern: str) -> bool:
    if "*" not in pattern:
        return name == pattern
    prefix, suffix = pattern.split("*", 1)
    return name.startswith(prefix) and name.endswith(suffix)


def _capture(name: str, pattern: str) -> str | None:
    if "*" not in pattern:
        return "" if name == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    end = len(name) - len(suffix) if suffix else len(name)
    if end < len(prefix):
        return None
    return name[len(prefix) : end]


def _substitute(pattern: str, capture: str) -> str:
    return pattern.replace("*", capture, 1)


def _raw_refspecs(
    repo: Repository,
    remote: str,
    plan: PushPlan,
    explicit_refspecs: Sequence[str],
    *,
    all_branches: bool,
    tags: bool,
) -> Tuple[str, ...]:
    values = list(explicit_refspecs)
    if plan.mode == "remote.push" and not values:
        values.extend(configured_push_refspecs(repo, remote))
    if all_branches:
        values.append("refs/heads/*:refs/heads/*")
    if tags:
        values.append("refs/tags/*:refs/tags/*")
    if plan.mode == "matching" and not values:
        values.append(":")
    return tuple(values)


def prune_specs(
    repo: Repository,
    remote: str,
    plan: PushPlan,
    explicit_refspecs: Sequence[str] = (),
    *,
    all_branches: bool = False,
    tags: bool = False,
) -> Tuple[PushSpec, ...]:
    """Return remote deletion specs implied by ``--prune``.

    Exact/default refspecs do not define a namespace to prune. Pattern refspecs,
    matching pushes, ``--all``, and ``--tags`` do. Negative refspecs protect
    excluded source refs from both updates and prune deletions.
    """

    raw_values = _raw_refspecs(
        repo,
        remote,
        plan,
        explicit_refspecs,
        all_branches=all_branches,
        tags=tags,
    )
    mappings = tuple(item for value in raw_values if (item := _mapping(value)) is not None)
    if not mappings:
        return ()
    negatives = _negative_patterns(raw_values)

    url = str(_settings(repo, remote)["url"])
    advertisement = SmartHttpPushClient(url).discover()
    local_names = {
        "heads": set(repo.refs.list_branches()),
        "tags": set(repo.refs.list_tags()),
    }
    existing_targets = {spec.target_ref for spec in plan.specs}
    deletions = []
    seen = set()

    for mapping in mappings:
        target_prefix = f"refs/{mapping.namespace}/"
        for remote_ref in sorted(advertisement.refs):
            if not remote_ref.startswith(target_prefix):
                continue
            target_name = remote_ref[len(target_prefix) :]
            capture = _capture(target_name, mapping.target)
            if capture is None:
                continue
            source_name = _substitute(mapping.source, capture)
            if any(
                namespace == mapping.namespace and _matches(source_name, pattern)
                for namespace, pattern in negatives
            ):
                continue
            if source_name in local_names[mapping.namespace]:
                continue
            target_ref = f"refs/{mapping.namespace}/{target_name}"
            if target_ref in existing_targets or target_ref in seen:
                continue
            seen.add(target_ref)
            deletions.append(
                PushSpec("", target_name, namespace=mapping.namespace, delete=True)
            )
    return tuple(deletions)
