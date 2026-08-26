"""Pathname decoration and boundary edges for ``rev-list --objects``.

Phase 75 intentionally kept ``rev_list_objects()`` as a compact OID/type API.
This module layers the user-facing Git-style object-name presentation on top so
existing Python callers keep their old data contract while the CLI can emit
pathname annotations and ``--objects-edge`` boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from .objects import CommitObject, TreeObject
from .repo import Repository
from .rev_list import rev_list, rev_list_objects


@dataclass(frozen=True)
class RevListNamedObjectEntry:
    """One selected object plus its first stable tree pathname, when known.

    ``path`` is ``None`` for commit objects and residual objects without a tree
    pathname.  The root tree uses the empty string, matching Git's ``OID SP``
    representation for a named root tree.
    """

    oid: str
    type_name: str
    path: Optional[str] = None


def _type_name(obj: object) -> str:
    value = getattr(obj, "type_name", None)
    if not isinstance(value, (bytes, bytearray)):
        raise RuntimeError("object has no valid type_name")
    return bytes(value).decode("ascii")


def _visit_tree(
    repo: Repository,
    tree_oid: str,
    path: str,
    *,
    selected: Set[str],
    seen: Set[str],
    output: List[RevListNamedObjectEntry],
) -> None:
    oid = tree_oid.lower()
    if oid not in selected or oid in seen:
        return

    tree = repo.store.read(oid)
    if not isinstance(tree, TreeObject):
        raise RuntimeError(f"Object {oid} referenced as a tree is not a tree")

    seen.add(oid)
    output.append(RevListNamedObjectEntry(oid=oid, type_name="tree", path=path))

    # TreeObject serialization is name-sorted, but sort explicitly here so
    # object-name output is deterministic even for hand-constructed objects.
    for entry in sorted(tree.entries, key=lambda item: item.name):
        child_oid = entry.sha.lower()
        if child_oid not in selected or child_oid in seen:
            continue
        child_path = entry.name if path == "" else f"{path}/{entry.name}"
        child = repo.store.read(child_oid)
        if isinstance(child, TreeObject):
            _visit_tree(
                repo,
                child_oid,
                child_path,
                selected=selected,
                seen=seen,
                output=output,
            )
            continue

        seen.add(child_oid)
        output.append(
            RevListNamedObjectEntry(
                oid=child_oid,
                type_name=_type_name(child),
                path=child_path,
            )
        )


def rev_list_named_objects(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
    reverse: bool = False,
    skip: int = 0,
    max_count: int = 0,
) -> Tuple[RevListNamedObjectEntry, ...]:
    """Return Phase-75's exact object set with deterministic path decoration.

    Commit selection and exclusion semantics remain delegated to
    :func:`rev_list_objects`.  Selected commits are emitted first in normal
    rev-list order.  Their trees are then walked pre-order; the first pathname
    that reaches a selected object becomes its stable annotation.  Any unusual
    selected object not reachable through that tree presentation is preserved
    at the end without a synthesized pathname.
    """

    base = rev_list_objects(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=reverse,
        skip=skip,
        max_count=max_count,
    )
    if not base:
        return ()

    selected = {entry.oid for entry in base}
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

    output: List[RevListNamedObjectEntry] = []
    seen: Set[str] = set()
    for entry in commits:
        oid = entry.oid.lower()
        if oid not in selected or oid in seen:
            continue
        obj = repo.store.read(oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(f"Object {oid} in rev-list commit output is not a commit")
        seen.add(oid)
        output.append(RevListNamedObjectEntry(oid=oid, type_name="commit"))

    for entry in commits:
        commit_oid = entry.oid.lower()
        if commit_oid not in selected:
            continue
        commit = repo.store.read(commit_oid)
        if not isinstance(commit, CommitObject):
            raise RuntimeError(f"Object {commit_oid} in rev-list traversal is not a commit")
        _visit_tree(
            repo,
            commit.tree.lower(),
            "",
            selected=selected,
            seen=seen,
            output=output,
        )

    # Preserve Phase 75's complete selected set even for uncommon graph shapes
    # such as an in-repository gitlink commit whose own tree was selected by the
    # generic reachability walker but has no ordinary pathname presentation.
    by_oid = {entry.oid: entry for entry in base}
    for entry in base:
        if entry.oid in seen:
            continue
        seen.add(entry.oid)
        output.append(
            RevListNamedObjectEntry(
                oid=entry.oid,
                type_name=by_oid[entry.oid].type_name,
                path=None,
            )
        )

    return tuple(output)


def rev_list_object_edges(
    repo: Repository,
    revisions: Sequence[str] = (),
    *,
    all_refs: bool = False,
    first_parent: bool = False,
    topo_order: bool = False,
) -> Tuple[str, ...]:
    """Return uninteresting boundary commits for ``--objects-edge``.

    Edges are parent commits immediately outside the complete, *unlimited*
    selected commit set.  Computing them before ``--skip``/``--max-count`` is
    important: Git still advertises the revision boundary even when the visible
    commit output is later limited.  Unrelated negative roots are therefore not
    emitted merely because they appeared on the command line.
    """

    full = rev_list(
        repo,
        revisions,
        all_refs=all_refs,
        first_parent=first_parent,
        topo_order=topo_order,
        reverse=False,
        skip=0,
        max_count=0,
        left_right=False,
    )
    selected = {entry.oid.lower() for entry in full}
    edges: List[str] = []
    seen: Set[str] = set()

    for entry in full:
        commit = repo.store.read(entry.oid)
        if not isinstance(commit, CommitObject):
            raise RuntimeError(f"Object {entry.oid} in rev-list traversal is not a commit")
        parents = commit.parents[:1] if first_parent else commit.parents
        for raw_parent in parents:
            parent = raw_parent.lower()
            if parent in selected or parent in seen:
                continue
            seen.add(parent)
            edges.append(parent)

    return tuple(edges)
