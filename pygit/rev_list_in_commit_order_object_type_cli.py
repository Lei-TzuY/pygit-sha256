"""Compose ``rev-list --in-commit-order`` with ``object:type`` filters.

Phase264 established a structured commit/snapshot-interleaved inventory for
ordered object traversal. Phase266 applies Git's ``object:type`` membership
rules directly to that inventory instead of reparsing rendered lines or adding a
second walker. Phase273 extends the same model to annotated tag roots: tag
objects explicitly supplied by positive revisions are inserted immediately after
the peeled commit frame, matching native ``rev-list --objects`` ordering.

Explicit positive roots preserve Git's default provided-object exemption unless
``--filter-provided-objects`` is requested; object-edge records remain an
independent presentation channel.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import rev_list_filter_cli as _filter
from . import rev_list_in_commit_order_cli as _ordered
from . import rev_list_promisor_cli as _promisor
from .objects import CommitObject, TagObject
from .plumbing import list_refs
from .promisor_object_inventory import PromisorObjectInventoryEntry
from .rev_list import _split_range
from .revision import resolve_revision


_IN_COMMIT_ORDER = "--in-commit-order"
_FILTER_PROVIDED = "--filter-provided-objects"
_FILTER_PRINT_OMITTED = "--filter-print-omitted"
_SUPPORTED_TYPES = {"commit", "tree", "blob", "tag"}


def _requested_type(argv: Sequence[str]) -> Optional[str]:
    if _IN_COMMIT_ORDER not in argv:
        return None
    filters = [arg for arg in argv if arg.startswith("--filter=")]
    if not filters:
        return None
    if len(filters) != 1:
        raise ValueError(
            "rev-list --in-commit-order accepts exactly one --filter action in this phase"
        )
    spec = filters[0].split("=", 1)[1]
    if not spec.startswith("object:type="):
        return None
    requested = spec.split("=", 1)[1]
    if requested not in _SUPPORTED_TYPES:
        supported = "|".join(sorted(_SUPPORTED_TYPES))
        raise ValueError(f"rev-list --in-commit-order supports object:type={supported}")
    return requested


def _ordered_projection(argv: Sequence[str]) -> list[str]:
    """Remove only object-type filter presentation arguments."""

    return [
        arg
        for arg in argv
        if not arg.startswith("--filter=")
        and arg not in {_FILTER_PROVIDED, _FILTER_PRINT_OMITTED}
    ]


def _positive_revision_names(repo, parsed) -> Tuple[str, ...]:
    """Return revision expressions that contribute provided positive roots.

    Tag objects are not part of commit ancestry, so the ordinary ordered
    inventory cannot discover them after the revision parser peels a commitish.
    Recover only the positive revision expressions here; negative sides stay
    exclusion-only and never manufacture visible tag objects.
    """

    revisions = tuple(parsed["revisions"])
    names: list[str] = []
    symmetric = [token for token in revisions if "..." in token]
    if symmetric:
        if len(revisions) != 1:
            raise ValueError("a symmetric A...B range cannot be mixed with other revisions")
        left, right = _split_range(symmetric[0], "...")
        names.extend((left, right))
    else:
        for token in revisions:
            if token.startswith("^"):
                continue
            if ".." in token:
                _left, right = _split_range(token, "..")
                names.append(right)
            else:
                names.append(token)

    if parsed["all_refs"]:
        names.extend(refname for _oid, refname in list_refs(repo))
    if not revisions and not parsed["all_refs"]:
        names.append("HEAD")

    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return tuple(result)


def _provided_tag_roots(repo, parsed):
    """Return ``peeled-commit -> tag OIDs`` plus the local tag identity set."""

    by_commit: dict[str, list[str]] = {}
    all_tags: list[str] = []
    seen_tags: set[str] = set()

    for revision in _positive_revision_names(repo, parsed):
        oid = resolve_revision(repo, revision).lower()
        chain: list[str] = []
        active: set[str] = set()
        while True:
            if oid in active:
                raise RuntimeError(f"tag cycle while resolving {revision!r}")
            active.add(oid)
            obj = repo.store.read(oid)
            if not isinstance(obj, TagObject):
                break
            if oid not in seen_tags:
                chain.append(oid)
                seen_tags.add(oid)
                all_tags.append(oid)
            oid = obj.target_sha.lower()

        if not chain:
            continue
        target = repo.store.read(oid)
        if not isinstance(target, CommitObject):
            raise ValueError(
                "rev-list --in-commit-order object:type=tag currently requires annotated tags that peel to commits"
            )
        by_commit.setdefault(oid, []).extend(chain)

    return (
        {commit: tuple(tags) for commit, tags in by_commit.items()},
        frozenset(all_tags),
    )


def _augment_with_provided_tags(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    by_commit: dict[str, Tuple[str, ...]],
) -> Tuple[PromisorObjectInventoryEntry, ...]:
    """Insert provided tag objects immediately after their peeled commit."""

    if not by_commit:
        return tuple(entries)
    output: list[PromisorObjectInventoryEntry] = []
    for entry in entries:
        output.append(entry)
        if (
            entry.type_name == "commit"
            and entry.path is None
            and entry.oid is not None
        ):
            for tag_oid in by_commit.get(entry.oid.lower(), ()):
                output.append(PromisorObjectInventoryEntry(type_name="tag", oid=tag_oid))
    return tuple(output)


def _keep_entry(
    entry: PromisorObjectInventoryEntry,
    *,
    requested: str,
    provided: frozenset[str],
) -> bool:
    """Apply Git object:type membership to one structured inventory entry."""

    if entry.oid is not None and entry.oid.lower() in provided:
        return True
    return entry.type_name == requested


def _apply_object_type(
    entries: Sequence[PromisorObjectInventoryEntry],
    *,
    requested: str,
    provided: frozenset[str],
) -> Tuple[PromisorObjectInventoryEntry, ...]:
    return tuple(
        entry
        for entry in entries
        if _keep_entry(entry, requested=requested, provided=provided)
    )


def try_run_rev_list_in_commit_order_object_type(
    argv: Sequence[str],
) -> Optional[int]:
    """Handle ordered ``object:type=commit|tree|blob|tag`` traversal.

    ``object:type`` filters do not populate Git's omitted-object set, so
    ``--filter-print-omitted`` is accepted here but intentionally adds no
    ``~<oid>`` records. Filtering happens before ordinary missing-object
    validation, allowing a requested type to discard promised objects of other
    known types without materialization.
    """

    requested = _requested_type(argv)
    if requested is None:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) > 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    filter_provided = _FILTER_PROVIDED in argv
    projected = _ordered_projection(argv)
    parsed = _ordered._parse(projected)
    if parsed is None:
        raise RuntimeError("ordered rev-list parser declined object:type projection")

    repo = _promisor._find_repo()
    entries, boundary_oids = _ordered._ordered_inventory(repo, parsed)

    tag_roots: dict[str, Tuple[str, ...]] = {}
    provided_tags = frozenset()
    if requested == "tag":
        tag_roots, provided_tags = _provided_tag_roots(repo, parsed)
        if not provided_tags:
            raise ValueError(
                "rev-list --in-commit-order object:type=tag currently requires an annotated-tag positive root"
            )
        entries = _augment_with_provided_tags(entries, by_commit=tag_roots)

    edges: Tuple[str, ...] = ()
    if parsed["in_commit_order_objects_edge"]:
        edges = _promisor._promisor_object_edges(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
        )
    if edges and boundary_oids:
        entries, boundary_oids = _ordered._dedupe_edge_boundary_overlap(
            entries,
            boundary_oids=boundary_oids,
            edges=edges,
        )

    provided = frozenset()
    if not filter_provided:
        provided = _filter._provided_commit_roots(repo, parsed) | provided_tags
    entries = _apply_object_type(
        entries,
        requested=requested,
        provided=provided,
    )

    return _ordered._render(
        entries,
        parsed=parsed,
        mode=parsed["in_commit_order_missing_mode"],
        boundary_oids=boundary_oids,
        edges=edges,
    )