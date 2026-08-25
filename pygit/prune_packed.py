"""Safely remove loose objects that have verified packed copies.

The pruning boundary is deliberately conservative. A loose object is eligible
only when at least one matching ``.pack``/``.idx`` pair validates completely and
agrees on object ID, offset, and CRC32. Corrupt or orphan pack files are ignored,
and malformed loose objects are never deleted.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .pack_index import parse_index
from .pack_plumbing import parse_pack
from .repo import Repository
from .store import ObjectStore


_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class PrunePackedResult:
    """Summary of one prune-packed pass."""

    scanned_loose: int
    packed_oids: int
    candidates: int
    pruned: int
    oids: Tuple[str, ...]
    ignored_packs: Tuple[str, ...]
    skipped_loose: Tuple[str, ...]
    dry_run: bool


def _is_hex(value: str) -> bool:
    return bool(value) and all(char in _HEX for char in value)


def _loose_objects(repo: Repository) -> Dict[str, Path]:
    """Return only canonical ``objects/aa/<62hex>`` loose-object paths."""
    result: Dict[str, Path] = {}
    root = repo.store.root
    if not root.exists():
        return result
    for directory in root.iterdir():
        name = directory.name
        if not directory.is_dir() or len(name) != 2 or not _is_hex(name):
            continue
        for path in directory.iterdir():
            suffix = path.name
            oid = name + suffix
            if path.is_file() and len(suffix) == 62 and _is_hex(suffix):
                result[oid] = path
    return result


def _pair_oids(idx_path: Path, pack_path: Path) -> Set[str]:
    """Validate one pair and return OIDs only if index and pack fully agree."""
    index = parse_index(idx_path)
    pack = parse_pack(pack_path)
    index_entries = {
        entry.oid: (entry.offset, entry.crc32) for entry in index.entries
    }
    pack_entries = {
        entry.oid: (entry.offset, entry.crc32) for entry in pack.entries
    }
    if index_entries != pack_entries:
        raise ValueError("pack index metadata does not match its pack file")
    return set(index_entries)


def _trusted_packed_oids(repo: Repository) -> tuple[Set[str], Tuple[str, ...]]:
    pack_dir = repo.store.root / "pack"
    if not pack_dir.exists():
        return set(), ()

    indexes = {path.stem: path for path in pack_dir.glob("*.idx") if path.is_file()}
    packs = {path.stem: path for path in pack_dir.glob("*.pack") if path.is_file()}
    trusted: Set[str] = set()
    ignored: List[str] = []

    for stem in sorted(set(indexes) | set(packs)):
        idx_path = indexes.get(stem)
        pack_path = packs.get(stem)
        if idx_path is None or pack_path is None:
            ignored.append(str((idx_path or pack_path).relative_to(repo.pygit_dir)))
            continue
        try:
            trusted.update(_pair_oids(idx_path, pack_path))
        except (OSError, ValueError, zlib.error):
            ignored.append(str(pack_path.relative_to(repo.pygit_dir)))
    return trusted, tuple(ignored)


def _valid_loose_copy(path: Path, oid: str) -> bool:
    """Verify the compressed loose image itself before deleting it."""
    try:
        compressed = path.read_bytes()
        decoder = zlib.decompressobj()
        store_bytes = decoder.decompress(compressed)
        store_bytes += decoder.flush()
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            return False
        if hashlib.sha256(store_bytes).hexdigest() != oid:
            return False
        ObjectStore._parse(store_bytes)
        return True
    except (OSError, ValueError, zlib.error, UnicodeError):
        return False


def prune_packed(repo: Repository, *, dry_run: bool = False) -> PrunePackedResult:
    """Remove verified loose duplicates of objects present in trusted packs.

    Invalid pack/index pairs do not contribute trusted OIDs. Invalid loose
    copies are reported in ``skipped_loose`` and left untouched. All candidates
    are validated before the first unlink so discovery never races with pruning.
    """
    loose = _loose_objects(repo)
    packed, ignored = _trusted_packed_oids(repo)
    eligible = sorted(set(loose) & packed)

    valid: List[tuple[str, Path]] = []
    skipped: List[str] = []
    for oid in eligible:
        path = loose[oid]
        if _valid_loose_copy(path, oid):
            valid.append((oid, path))
        else:
            skipped.append(oid)

    if not dry_run:
        for _, path in valid:
            path.unlink()
        for directory in sorted({path.parent for _, path in valid}):
            try:
                directory.rmdir()
            except OSError:
                pass

    oids = tuple(oid for oid, _ in valid)
    return PrunePackedResult(
        scanned_loose=len(loose),
        packed_oids=len(packed),
        candidates=len(eligible),
        pruned=0 if dry_run else len(valid),
        oids=oids,
        ignored_packs=ignored,
        skipped_loose=tuple(skipped),
        dry_run=dry_run,
    )
