"""Object-store enumeration strategies used by batch plumbing.

The default ``cat-file --batch-all-objects`` contract is hash-order output.
``--unordered`` intentionally relaxes that ordering so objects can be visited in
storage-local order without first materializing and sorting the complete store.
"""

from __future__ import annotations

from typing import Iterable

from .pack import PackReader
from .repo import Repository


_HEX = frozenset("0123456789abcdef")


def _canonical_oid(oid: str) -> bool:
    return len(oid) == 64 and all(char in _HEX for char in oid)


def iter_object_ids(repo: Repository, *, unordered: bool = False) -> Iterable[str]:
    """Yield each locally stored canonical object ID exactly once.

    ``unordered=False`` preserves the existing deterministic hash-order
    contract. ``unordered=True`` avoids the global sort: loose objects are
    emitted while walking loose storage, followed by each pack in path order
    with entries visited by pack offset. A ``seen`` set is still required to
    suppress loose/packed and multi-pack duplicates, but no complete sortable
    object list is built.

    The unordered sequence is deliberately unspecified to callers; its only
    guarantees are complete local coverage, canonical IDs, and deduplication.
    """

    if not unordered:
        # ObjectStore.all_shas() is the canonical sorted loose+packed view.
        for oid in repo.store.all_shas():
            if _canonical_oid(oid):
                yield oid
        return

    seen: set[str] = set()
    root = repo.store.root

    # Visit loose objects directly instead of ObjectStore.all_shas(), whose
    # implementation necessarily builds and globally sorts a set.
    if root.is_dir():
        for prefix_dir in root.iterdir():
            prefix = prefix_dir.name
            if not prefix_dir.is_dir() or len(prefix) != 2 or any(char not in _HEX for char in prefix):
                continue
            for obj_file in prefix_dir.iterdir():
                if not obj_file.is_file():
                    continue
                oid = prefix + obj_file.name
                if not _canonical_oid(oid) or oid in seen:
                    continue
                seen.add(oid)
                yield oid

    # Group reads by pack and follow physical entry offsets inside each pack.
    # This is the locality win that makes --unordered useful for content-heavy
    # batch scans rather than merely changing presentation order.
    pack_dir = root / "pack"
    if not pack_dir.is_dir():
        return
    for idx_file in sorted(pack_dir.glob("*.idx")):
        reader = PackReader(idx_file)
        for oid in sorted(reader.get_shas(), key=reader._offsets.__getitem__):
            if not _canonical_oid(oid) or oid in seen:
                continue
            seen.add(oid)
            yield oid
