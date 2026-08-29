"""Metadata-only object inventory for partial-clone repositories.

The normal ``rev-list --objects`` implementation intentionally works in pygit's
repository-visible SHA-256 object domain.  A filtered foreign tree, however, can
contain entries whose blob content has not arrived yet; those entries only have
the promisor transport's native SHA-1 identity until materialization computes a
real local SHA-256 object id.

This module exposes that distinction explicitly instead of faulting promised
objects in or inventing surrogate SHA-256 ids.  It is the substrate for future
``rev-list --missing`` presentation and bulk-prefetch planning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .objects import CommitObject, TagObject, TreeObject
from .promisor import promised_kind
from .repo import Repository
from .rev_list import _object_exclusion_roots, _shallow_boundaries, rev_list


_MODE_TYPE = {
    "040000": "tree",
    "100644": "blob",
    "100755": "blob",
    "120000": "blob",
    "160000": "commit",
}


@dataclass(frozen=True)
class PromisorObjectInventoryEntry:
    """One reachable object without collapsing local/native hash domains.

    ``oid`` is a repository-visible SHA-256 id and is therefore populated only
    for materialized objects. ``native_oid`` is populated only for an unresolved
    promised object and remains a transport/interoperability identity. ``path``
    is the first stable tree pathname that reached the object; commits use
    ``None`` and root trees use the empty string.
    """

    type_name: str
    oid: Optional[str] = None
    native_oid: Optional[str] = None
    path: Optional[str] = None

    @property
    def missing(self) -> bool:
        return self.oid is None


def _type_name(obj: object) -> str:
    value = getattr(obj, "type_name", None)
    if not isinstance(value, (bytes, bytearray)):
        raise RuntimeError("object has no valid type_name")
    return bytes(value).decode("ascii")


def _entry_type(mode: str) -> str:
    try:
        return _MODE_TYPE[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported tree entry mode: {mode!r}") from exc


def _key(entry: PromisorObjectInventoryEntry) -> tuple[str, str]:
    if entry.oid is not None:
        return ("local", entry.oid)
    assert entry.native_oid is not None
    return ("native", entry.native_oid)


def _append_unique(
    output: List[PromisorObjectInventoryEntry],
    seen: Set[tuple[str, str]],
    entry: PromisorObjectInventoryEntry,
) -> bool:
    key = _key(entry)
    if key in seen:
        return False
    seen.add(key)
    output.append(entry)
    return True


def _walk_tree(
    repo: Repository,
    tree_oid: str,
    path: str,
    *,
    output: List[PromisorObjectInventoryEntry],
    seen: Set[tuple[str, str]],
    active: Set[str],
) -> None:
    oid = tree_oid.lower()
    if oid in active:
        raise RuntimeError("tree cycle while building promisor object inventory")
    tree = repo.store.read(oid)
    if not isinstance(tree, TreeObject):
        raise RuntimeError(f"Object {oid} referenced as a tree is not a tree")

    _append_unique(
        output,
        seen,
        PromisorObjectInventoryEntry(type_name="tree", oid=oid, path=path),
    )
    next_active = set(active)
    next_active.add(oid)

    for item in sorted(tree.entries, key=lambda entry: entry.name):
        child_path = item.name if path == "" else f"{path}/{item.name}"
        kind = _entry_type(item.mode)

        if not item.is_resolved and item.native_oid:
            native_oid = item.native_oid.lower()
            promised = promised_kind(repo.pygit_dir, native_oid)
            if promised is not None:
                _append_unique(
                    output,
                    seen,
                    PromisorObjectInventoryEntry(
                        type_name=kind,
                        native_oid=native_oid,
                        path=child_path,
                    ),
                )
                continue

        child_oid = item.sha.lower()
        child = repo.store.read(child_oid)
        if isinstance(child, TreeObject):
            _walk_tree(
                repo,
                child_oid,
                child_path,
                output=output,
                seen=seen,
                active=next_active,
            )
            continue
        _append_unique(
            output,
            seen,
            PromisorObjectInventoryEntry(
                type_name=_type_name(child),
                oid=child_oid,
                path=child_path,
            ),
        )


def _walk_commit_closure(
    repo: Repository,
    roots: Iterable[str],
    *,
    first_parent: bool,
) -> Tuple[PromisorObjectInventoryEntry, ...]:
    """Walk complete commit/tree closure for exclusion-set accounting."""
    output: List[PromisorObjectInventoryEntry] = []
    seen: Set[tuple[str, str]] = set()
    visited_commits: Set[str] = set()
    shallow = _shallow_boundaries(repo)
    pending = [oid.lower() for oid in roots]

    while pending:
        oid = pending.pop()
        if oid in visited_commits:
            continue
        visited_commits.add(oid)
        obj = repo.store.read(oid)
        if isinstance(obj, TagObject):
            target = obj.target_sha.lower()
            _append_unique(
                output,
                seen,
                PromisorObjectInventoryEntry(type_name="tag", oid=oid),
            )
            pending.append(target)
            continue
        if not isinstance(obj, CommitObject):
            _append_unique(
                output,
                seen,
                PromisorObjectInventoryEntry(type_name=_type_name(obj), oid=oid),
            )
            continue

        _append_unique(
            output,
            seen,
            PromisorObjectInventoryEntry(type_name="commit", oid=oid),
        )
        _walk_tree(
            repo,
            obj.tree.lower(),
            "",
            output=output,
            seen=seen,
            active=set(),
        )
        if oid in shallow:
            continue
        parents = obj.parents[:1] if first_parent else obj.parents
        pending.extend(parent.lower() for parent in parents)

    return tuple(output)


def promisor_object_inventory(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
    snapshot_commits: Optional[Sequence[str]] = None,
) -> Tuple[PromisorObjectInventoryEntry, ...]:
    """Return selected commits plus reachable present and promised objects.

    Commit selection follows :func:`pygit.rev_list.rev_list`, including shallow
    boundaries and revision exclusions. Selected commit ids are emitted first.
    Tree/blob closure normally follows those selected commits in the same order.
    ``snapshot_commits`` can override only that snapshot traversal order; this is
    used by boundary-aware object presentation where an excluded boundary commit
    contributes its own tree snapshot without becoming a selected commit or
    recursively pulling older history into the object walk.

    Explicit negative revisions (and common ancestry in a symmetric range)
    still subtract their complete object closure after snapshot traversal.

    Crucially, unresolved promised entries are *reported*, not materialized.
    Their native SHA-1 is kept in ``native_oid`` while ``oid`` remains ``None``;
    callers therefore cannot accidentally treat a transport identity as a local
    SHA-256 object id.
    """
    commits = rev_list(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=skip,
        max_count=max_count,
        left_right=False,
    )
    if not commits:
        return ()

    output: List[PromisorObjectInventoryEntry] = []
    seen: Set[tuple[str, str]] = set()

    selected_oids: List[str] = []
    for selected in commits:
        oid = selected.oid.lower()
        selected_oids.append(oid)
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list traversal is not a commit")
        _append_unique(
            output,
            seen,
            PromisorObjectInventoryEntry(type_name="commit", oid=oid),
        )

    snapshot_roots = (
        selected_oids
        if snapshot_commits is None
        else [oid.lower() for oid in snapshot_commits]
    )
    for snapshot_oid in snapshot_roots:
        commit = repo.store.read(snapshot_oid)
        if not isinstance(commit, CommitObject):
            raise RuntimeError(
                f"Object {snapshot_oid} in snapshot traversal is not a commit"
            )
        _walk_tree(
            repo,
            commit.tree.lower(),
            "",
            output=output,
            seen=seen,
            active=set(),
        )

    exclusion_roots = _object_exclusion_roots(
        repo,
        revisions,
        first_parent=first_parent,
    )
    if exclusion_roots:
        excluded = {
            _key(entry)
            for entry in _walk_commit_closure(
                repo,
                exclusion_roots,
                first_parent=first_parent,
            )
        }
        output = [entry for entry in output if _key(entry) not in excluded]

    return tuple(output)
