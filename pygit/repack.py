"""Safe pack maintenance for ``pygit repack``.

The implementation is intentionally conservative: it refuses to mutate an
unhealthy repository, validates every old and newly generated pack/index pair,
and never removes an old pack unless the newly installed pack contains every
object that old pair carried.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .fsck import fsck
from .pack import PackWriter
from .pack_index import parse_index
from .pack_plumbing import parse_pack
from .prune_packed import _loose_objects, _valid_loose_copy, prune_packed
from .repo import Repository


@dataclass(frozen=True)
class RepackResult:
    """Summary of one repack operation."""

    reachable: int
    already_packed: int
    object_count: int
    selected_oids: Tuple[str, ...]
    pack_hash: Optional[str]
    pack_path: Optional[Path]
    idx_path: Optional[Path]
    removed_packs: Tuple[str, ...]
    loose_candidates: Tuple[str, ...]
    pruned_loose: int
    dry_run: bool


@dataclass(frozen=True)
class _PackPair:
    pack_path: Path
    idx_path: Path
    oids: frozenset[str]

    @property
    def stem(self) -> str:
        return self.pack_path.stem


def _pair_oids(pack_path: Path, idx_path: Path) -> frozenset[str]:
    """Strictly validate one pack/index pair and return its exact object set."""
    pack = parse_pack(pack_path)
    index = parse_index(idx_path)
    pack_meta = {entry.oid: (entry.offset, entry.crc32) for entry in pack.entries}
    index_meta = {entry.oid: (entry.offset, entry.crc32) for entry in index.entries}
    if pack_meta != index_meta:
        raise ValueError(f"pack/index metadata mismatch for {pack_path.name}")
    return frozenset(pack_meta)


def _pack_pairs(repo: Repository) -> Tuple[_PackPair, ...]:
    """Return every complete, strictly validated pair or fail closed."""
    pack_dir = repo.store.root / "pack"
    if not pack_dir.exists():
        return ()

    packs = {path.stem: path for path in pack_dir.glob("*.pack") if path.is_file()}
    indexes = {path.stem: path for path in pack_dir.glob("*.idx") if path.is_file()}
    missing = sorted(set(packs) ^ set(indexes))
    if missing:
        raise RuntimeError(f"orphan pack/index file prevents repack: {missing[0]}")

    pairs: List[_PackPair] = []
    for stem in sorted(packs):
        pack_path = packs[stem]
        idx_path = indexes[stem]
        pairs.append(_PackPair(pack_path, idx_path, _pair_oids(pack_path, idx_path)))
    return tuple(pairs)


def _objects_for_pack(repo: Repository, oids: Tuple[str, ...]):
    result = []
    for oid in oids:
        obj = repo.store.read(oid)
        actual = obj.hash()
        if actual != oid:
            raise ValueError(f"object {oid} re-serializes as {actual}")
        result.append((oid, obj))
    return result


def _validate_new_pair(pack_path: Path, idx_path: Path, expected: Set[str]) -> None:
    actual = set(_pair_oids(pack_path, idx_path))
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append(f"missing {missing[0]}")
        if extra:
            detail.append(f"unexpected {extra[0]}")
        raise ValueError("generated repack does not match selection" + (": " + ", ".join(detail) if detail else ""))


def _install_pack(repo: Repository, selected: Tuple[str, ...]) -> tuple[str, Path, Path]:
    """Generate, validate, and install one deterministic pack pair."""
    pack_dir = repo.store.root / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    objects = _objects_for_pack(repo, selected)

    with tempfile.TemporaryDirectory(prefix=".repack-", dir=str(pack_dir)) as temp_name:
        temp_dir = Path(temp_name)
        temp_pack, temp_idx = PackWriter(objects).write_pack_and_idx(temp_dir, "pack")
        _validate_new_pair(temp_pack, temp_idx, set(selected))

        final_pack = pack_dir / temp_pack.name
        final_idx = pack_dir / temp_idx.name
        pack_hash = temp_pack.stem.split("-", 1)[1]

        if final_pack.exists() or final_idx.exists():
            if not (final_pack.is_file() and final_idx.is_file()):
                raise RuntimeError(f"incomplete existing repack target: {final_pack.stem}")
            if final_pack.read_bytes() != temp_pack.read_bytes() or final_idx.read_bytes() != temp_idx.read_bytes():
                raise RuntimeError(f"repack target collision: {final_pack.stem}")
        else:
            os.replace(temp_pack, final_pack)
            try:
                os.replace(temp_idx, final_idx)
            except Exception:
                try:
                    final_pack.unlink()
                except OSError:
                    pass
                raise

        _validate_new_pair(final_pack, final_idx, set(selected))
        return pack_hash, final_pack, final_idx


def _loose_candidates(repo: Repository, packed_after: Set[str]) -> Tuple[str, ...]:
    loose = _loose_objects(repo)
    candidates = []
    for oid in sorted(set(loose) & packed_after):
        if _valid_loose_copy(loose[oid], oid):
            candidates.append(oid)
    return tuple(candidates)


def repack(
    repo: Repository,
    *,
    all_objects: bool = False,
    delete_redundant: bool = False,
    dry_run: bool = False,
) -> RepackResult:
    """Create a verified pack from reachable objects and optionally compact storage.

    By default only reachable objects without a trusted packed copy are selected.
    ``all_objects=True`` repacks the complete reachable closure into one pack.
    ``delete_redundant=True`` removes only old verified pairs whose complete OID
    set is contained in the newly installed pack, then prunes verified loose
    duplicates. Unreachable objects that exist only in an old pack therefore
    prevent that pack from being deleted.
    """
    report = fsck(repo)
    if report.errors:
        raise RuntimeError(f"cannot repack an unhealthy repository: {report.errors[0].render()}")

    old_pairs = _pack_pairs(repo)
    packed_before: Set[str] = set()
    for pair in old_pairs:
        packed_before.update(pair.oids)

    reachable = set(report.reachable)
    selected_set = reachable if all_objects else reachable - packed_before
    selected = tuple(sorted(selected_set))

    removable = tuple(
        pair for pair in old_pairs
        if delete_redundant and selected_set and set(pair.oids).issubset(selected_set)
    )
    removed_names = tuple(pair.pack_path.name for pair in removable)
    packed_after = packed_before | selected_set
    loose_candidates = _loose_candidates(repo, packed_after) if delete_redundant else ()

    if dry_run:
        return RepackResult(
            reachable=len(reachable),
            already_packed=len(reachable & packed_before),
            object_count=len(selected),
            selected_oids=selected,
            pack_hash=None,
            pack_path=None,
            idx_path=None,
            removed_packs=removed_names,
            loose_candidates=loose_candidates,
            pruned_loose=0,
            dry_run=True,
        )

    pack_hash: Optional[str] = None
    final_pack: Optional[Path] = None
    final_idx: Optional[Path] = None
    if selected:
        pack_hash, final_pack, final_idx = _install_pack(repo, selected)

    actually_removed: List[str] = []
    if delete_redundant and selected:
        assert final_pack is not None
        final_stem = final_pack.stem
        # Revalidate immediately before each destructive step. Delete the index
        # first so an interrupted removal can leave only an ignored orphan pack.
        for pair in removable:
            if pair.stem == final_stem:
                continue
            current_oids = _pair_oids(pair.pack_path, pair.idx_path)
            if not set(current_oids).issubset(selected_set):
                raise RuntimeError(f"pack changed during repack: {pair.pack_path.name}")
            pair.idx_path.unlink()
            pair.pack_path.unlink()
            actually_removed.append(pair.pack_path.name)

    prune_result = prune_packed(repo) if delete_redundant and selected else None
    return RepackResult(
        reachable=len(reachable),
        already_packed=len(reachable & packed_before),
        object_count=len(selected),
        selected_oids=selected,
        pack_hash=pack_hash,
        pack_path=final_pack,
        idx_path=final_idx,
        removed_packs=tuple(actually_removed),
        loose_candidates=loose_candidates,
        pruned_loose=prune_result.pruned if prune_result is not None else 0,
        dry_run=False,
    )
