"""Reachability names for ``fsck --name-objects`` diagnostics.

The core fsck engine intentionally deals in object IDs.  This presentation
layer reconstructs deterministic rev-parse-style names for reachable objects so
integrity diagnostics can point back to a human-meaningful path without
changing storage validation or reachability decisions.
"""

from __future__ import annotations

import heapq
from typing import Dict, Optional, Tuple

from .fsck import FsckReport
from .objects import CommitObject, TagObject, TreeObject
from .repo import Repository


def _root_name(source: str) -> Optional[str]:
    """Return a useful rev-parse-style spelling for one fsck root source."""
    if source == "HEAD" or source.startswith("refs/"):
        return source
    if source.startswith("argument:"):
        parts = source.split(":", 2)
        return parts[2] if len(parts) == 3 and parts[2] else None
    if source.startswith("index:"):
        path = source[len("index:") :]
        return f":{path}" if path else None
    # Reflog roots currently record parser-position metadata rather than the
    # user-facing @{N} ordinal.  Do not invent a misleading rev-parse name.
    return None


def _quality(name: str) -> Tuple[int, int, str]:
    """Prefer shorter/shallower deterministic names when an object has many."""
    return (name.count("^") + name.count("~") + name.count(":"), len(name), name)


def _tree_path_name(base: str, path: str) -> str:
    if ":" in base and not base.endswith("^{tree}"):
        # Index roots already use :path and do not support tree-ish suffixes.
        return f"{base}/{path}" if path else base
    if base.endswith("^{tree}"):
        commitish = base[: -len("^{tree}")]
        return f"{commitish}:{path}" if path else base
    return f"{base}:{path}" if path else f"{base}^{{tree}}"


def reachable_object_names(repo: Repository, report: FsckReport) -> Dict[str, str]:
    """Return deterministic names for readable reachable objects.

    Names are seeded from HEAD, refs, explicit fsck arguments, and index roots.
    Commit ancestry uses ``~1`` for the first parent and ``^N`` for additional
    parents; commit trees and tree entries use normal ``^{tree}`` / ``:path``
    spellings. Annotated tags peel through ``^{}``. Objects that are reachable
    only from an unnamed recovery root (for example a reflog parser position)
    deliberately remain unnamed rather than receiving a fake spelling.
    """

    best: Dict[str, str] = {}
    queue: list[Tuple[Tuple[int, int, str], str, str]] = []

    def offer(oid: str, name: str) -> None:
        oid = oid.lower()
        if oid not in report.reachable:
            return
        current = best.get(oid)
        if current is not None and _quality(current) <= _quality(name):
            return
        best[oid] = name
        heapq.heappush(queue, (_quality(name), oid, name))

    for source, oid in sorted(report.roots.items()):
        name = _root_name(source)
        if name is not None:
            offer(oid, name)

    expanded: set[Tuple[str, str]] = set()
    while queue:
        _, oid, name = heapq.heappop(queue)
        if best.get(oid) != name or (oid, name) in expanded:
            continue
        expanded.add((oid, name))
        try:
            obj = repo.store.read(oid)
        except Exception:
            continue

        if isinstance(obj, CommitObject):
            offer(obj.tree, f"{name}^{{tree}}")
            for index, parent in enumerate(obj.parents, 1):
                suffix = "~1" if index == 1 else f"^{index}"
                offer(parent, f"{name}{suffix}")
            continue

        if isinstance(obj, TagObject):
            offer(obj.target_sha, f"{name}^{{}}")
            continue

        if isinstance(obj, TreeObject):
            for entry in sorted(obj.entries, key=lambda item: item.name):
                offer(entry.sha, _tree_path_name(name, entry.name))

    return best


def render_issue_with_name(issue, names: Dict[str, str]) -> str:
    """Render one fsck issue, adding a reachable name after its OID."""
    location = issue.oid or issue.source
    prefix = f"{issue.severity}: {issue.code}"
    if location:
        prefix += f" {location}"
        if issue.oid and issue.oid.lower() in names:
            prefix += f" ({names[issue.oid.lower()]})"
    return f"{prefix}: {issue.message}"


__all__ = ["reachable_object_names", "render_issue_with_name"]
