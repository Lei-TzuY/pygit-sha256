"""Promisor-aware integrity checking for partial clones.

Native-reference trees deliberately retain upstream SHA-1 child identities for
objects omitted by a promisor remote.  Ordinary fsck must not dereference those
entries through ``TreeEntry.sha``: doing so would turn an integrity check into a
network fetch and would incorrectly require a local SHA-256 identity for content
that is intentionally absent.

This module wraps the existing fsck implementation only for promisor
repositories.  Present objects continue to be validated by the original
SHA-256 checker; unresolved native tree entries are accepted only when the
promisor metadata records the same expected object kind.  No surrogate local
OID is invented for an absent foreign object.
"""

from __future__ import annotations

import importlib
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .objects import CommitObject, GitObject, TagObject, TreeObject
from .promisor import is_promisor_repository, promised_kind
from .repo import Repository


_core = importlib.import_module(".fsck", __package__)
_INSTALLED = False
_HEX = frozenset("0123456789abcdef")


def _is_native_oid(value: Optional[str]) -> bool:
    return bool(
        value
        and len(value) == 40
        and all(char in _HEX for char in value.lower())
    )


def _validate_promisor_tree(
    repo: Repository,
    report,
    oid: str,
    obj: TreeObject,
    edges: Dict[str, List[Tuple[str, str, str]]],
) -> None:
    """Validate a tree without materializing unresolved native entries."""
    try:
        reconstructed = obj.hash()
        if reconstructed != oid:
            _core._issue(
                report,
                "error",
                "noncanonical-object",
                f"re-serialized hash is {reconstructed}",
                oid=oid,
            )
    except Exception as exc:
        _core._issue(report, "error", "malformed-object", str(exc), oid=oid)

    names: Set[str] = set()
    for entry in obj.entries:
        if entry.name in names:
            _core._issue(
                report,
                "error",
                "duplicate-tree-entry",
                f"duplicate name {entry.name!r}",
                oid=oid,
            )
        names.add(entry.name)
        if (
            not entry.name
            or entry.name in {".", ".."}
            or "/" in entry.name
            or "\x00" in entry.name
        ):
            _core._issue(
                report,
                "error",
                "bad-tree-name",
                f"invalid entry name {entry.name!r}",
                oid=oid,
            )
        if entry.mode not in _core._TREE_MODES:
            _core._issue(
                report,
                "error",
                "bad-tree-mode",
                f"unsupported mode {entry.mode!r} for {entry.name!r}",
                oid=oid,
            )
            continue

        expected = _core._TYPE_BY_MODE[entry.mode]
        if entry.is_resolved:
            # Reading sha is safe once the store filled the persistent
            # native->local mapping; it cannot invoke the lazy materializer.
            _core._edge(
                report,
                edges,
                oid,
                entry.sha,
                expected,
                f"tree entry {entry.name}",
            )
            continue

        native_oid = entry.native_oid
        if not _is_native_oid(native_oid):
            _core._issue(
                report,
                "error",
                "bad-promisor-object-id",
                f"tree entry {entry.name} has no valid native SHA-1 identity",
                oid=oid,
            )
            continue

        promised = promised_kind(repo.pygit_dir, native_oid)
        if promised is None:
            _core._issue(
                report,
                "error",
                "missing-promisor-object",
                (
                    f"tree entry {entry.name} references native object "
                    f"{native_oid} that is absent and not recorded as promised"
                ),
                oid=oid,
            )
            continue
        if promised != expected:
            _core._issue(
                report,
                "error",
                "wrong-promisor-type",
                (
                    f"tree entry {entry.name} expects {expected}, but native "
                    f"promisor metadata records {promised} for {native_oid}"
                ),
                oid=oid,
            )


def _validate_object(
    repo: Repository,
    report,
    oid: str,
    obj: GitObject,
    edges: Dict[str, List[Tuple[str, str, str]]],
    shallow: Set[str],
) -> None:
    if isinstance(obj, TreeObject):
        _validate_promisor_tree(repo, report, oid, obj, edges)
        return
    _core._validate_object(repo, report, oid, obj, edges, shallow)


def _connectivity_storage(
    repo: Repository,
    roots: Sequence[str],
    shallow: Set[str],
) -> Set[str]:
    """Collect locally addressable reachable objects without demand-fetching."""
    storage: Set[str] = set()
    queue = list(roots)
    seen: Set[str] = set()
    while queue:
        oid = queue.pop()
        if oid in seen:
            continue
        seen.add(oid)
        storage.add(oid)
        try:
            obj = repo.store.read(oid)
        except Exception:
            continue
        if isinstance(obj, CommitObject):
            queue.append(obj.tree)
            if oid not in shallow:
                queue.extend(obj.parents)
        elif isinstance(obj, TreeObject):
            # Local trees and already-resolved native entries have a real
            # repository SHA-256.  Unresolved promises have only a transport
            # SHA-1, so they intentionally do not enter the local traversal.
            queue.extend(entry.sha for entry in obj.entries if entry.is_resolved)
        elif isinstance(obj, TagObject):
            queue.append(obj.target_sha)
    return storage


def _promisor_fsck(
    repo: Repository,
    *,
    connectivity_only: bool = False,
    include_reflogs: bool = False,
    heads: Sequence[str] = (),
    include_index: Optional[bool] = None,
):
    report = _core.FsckReport()
    shallow = _core._shallow_boundaries(repo, report)
    explicit = bool(heads)
    if explicit:
        _core._explicit_roots(repo, report, heads)
        if include_index is True:
            _core._index_roots(repo, report)
    else:
        _core._ref_roots(repo, report)
        if include_index is not False:
            _core._index_roots(repo, report)
        if include_reflogs:
            _core._reflog_roots(repo, report)
        for oid in sorted(shallow):
            _core._add_root(report, f"shallow:{oid}", oid)

    if connectivity_only:
        storage = _connectivity_storage(
            repo,
            list(report.roots.values()),
            shallow,
        )
    else:
        storage = _core._loose_oids(repo, report) | _core._packed_oids(repo, report)

    objects: Dict[str, GitObject] = {}
    edges: Dict[str, List[Tuple[str, str, str]]] = {}
    for oid in sorted(storage | set(report.roots.values())):
        try:
            obj = repo.store.read(oid)
        except Exception as exc:
            _core._issue(report, "error", "object-read", str(exc), oid=oid)
            continue
        objects[oid] = obj
        report.checked_objects.add(oid)
        _validate_object(repo, report, oid, obj, edges, shallow)

    for source, outgoing in sorted(edges.items()):
        for target, expected, relation in outgoing:
            target_obj = objects.get(target)
            if target_obj is None:
                if target in storage or repo.store.exists(target):
                    try:
                        target_obj = repo.store.read(target)
                        objects[target] = target_obj
                        report.checked_objects.add(target)
                    except Exception as exc:
                        _core._issue(report, "error", "object-read", str(exc), oid=target)
                        continue
                else:
                    _core._issue(
                        report,
                        "error",
                        "missing-object",
                        f"{source[:12]} {relation} references a missing object",
                        oid=target,
                    )
                    continue
            if expected != "object" and _core._object_type(target_obj) != expected:
                _core._issue(
                    report,
                    "error",
                    "wrong-object-type",
                    (
                        f"{source[:12]} {relation} expects {expected}, "
                        f"found {_core._object_type(target_obj)}"
                    ),
                    oid=target,
                )

    _core._detect_cycles(report, edges, objects)

    queue = list(report.roots.values())
    while queue:
        oid = queue.pop()
        if oid in report.reachable:
            continue
        report.reachable.add(oid)
        for target, _, _ in edges.get(oid, ()):
            queue.append(target)

    all_known = set(storage) | set(objects)
    report.unreachable = all_known - report.reachable
    incoming_unreachable: Set[str] = set()
    for source, outgoing in edges.items():
        if source not in report.unreachable:
            continue
        incoming_unreachable.update(
            target for target, _, _ in outgoing if target in report.unreachable
        )
    report.dangling = report.unreachable - incoming_unreachable
    return report


def install_promisor_fsck_support() -> None:
    """Install a transparent fsck wrapper while preserving ordinary repos."""
    global _INSTALLED
    if _INSTALLED:
        return

    original = _core.fsck

    def fsck(
        repo: Repository,
        *,
        connectivity_only: bool = False,
        include_reflogs: bool = False,
        heads: Sequence[str] = (),
        include_index: Optional[bool] = None,
    ):
        if not is_promisor_repository(repo.pygit_dir):
            return original(
                repo,
                connectivity_only=connectivity_only,
                include_reflogs=include_reflogs,
                heads=heads,
                include_index=include_index,
            )
        return _promisor_fsck(
            repo,
            connectivity_only=connectivity_only,
            include_reflogs=include_reflogs,
            heads=heads,
            include_index=include_index,
        )

    _core.fsck = fsck
    _INSTALLED = True
