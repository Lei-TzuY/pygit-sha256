"""Object-store enumeration strategies used by batch plumbing.

The default ``cat-file --batch-all-objects`` contract is hash-order output.
``--unordered`` intentionally relaxes that ordering so objects can be visited in
storage-local order without first materializing and sorting the complete store.
Both modes include configured alternate object databases, matching Git's
``--batch-all-objects`` visibility semantics.
"""

from __future__ import annotations

from typing import Iterable

from .pack import PackReader
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


def _canonical_oid(oid: str) -> bool:
    return len(oid) == 64 and all(char in _HEX for char in oid)


def _iter_root_local_ids(root) -> Iterable[str]:
    """Yield one object database in storage-local order, without alternates."""
    if root.is_dir():
        for prefix_dir in root.iterdir():
            prefix = prefix_dir.name
            if (
                not prefix_dir.is_dir()
                or len(prefix) != 2
                or any(char not in _HEX for char in prefix)
            ):
                continue
            for obj_file in prefix_dir.iterdir():
                if not obj_file.is_file():
                    continue
                oid = prefix + obj_file.name
                if _canonical_oid(oid):
                    yield oid

    pack_dir = root / "pack"
    if not pack_dir.is_dir():
        return
    for idx_file in sorted(pack_dir.glob("*.idx")):
        reader = PackReader(idx_file)
        for oid in sorted(reader.get_shas(), key=reader._offsets.__getitem__):
            if _canonical_oid(oid):
                yield oid


def iter_object_ids(repo: Repository, *, unordered: bool = False) -> Iterable[str]:
    """Yield each accessible canonical object ID exactly once.

    ``unordered=False`` uses :meth:`ObjectStore.all_shas`, so primary and
    alternate databases are globally hash-sorted. ``unordered=True`` avoids the
    global sort and visits each storage root in primary-then-alternate order,
    loose objects before packs, while a ``seen`` set suppresses duplicates
    across loose storage, packs, MIDX overlap, and alternates.
    """
    if not unordered:
        for oid in repo.store.all_shas():
            if _canonical_oid(oid):
                yield oid
        return

    seen: set[str] = set()
    for root in repo.store.storage_roots():
        for oid in _iter_root_local_ids(root):
            if oid in seen:
                continue
            seen.add(oid)
            yield oid
