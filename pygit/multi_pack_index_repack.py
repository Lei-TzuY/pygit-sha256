"""Verified pack consolidation for ``pygit multi-pack-index repack``."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .multi_pack_index import (
    ParsedMultiPackIndex,
    _build_bytes,
    _encode_pack_names,
    verify_multi_pack_index,
)
from .pack import PackReader, PackWriter
from .pack_index import parse_index
from .pack_plumbing import parse_pack
from .repo import Repository


@dataclass(frozen=True)
class MultiPackIndexRepackResult:
    """Summary of one successful MIDX repack pass."""

    path: Path
    batch_size: int
    selected_packs: Tuple[str, ...]
    expected_size: int
    object_count: int
    pack_path: Optional[Path]
    idx_path: Optional[Path]

    @property
    def created(self) -> bool:
        return self.pack_path is not None


@dataclass(frozen=True)
class _PackCandidate:
    idx_name: str
    expected_size: int
    mtime_ns: int


def _pack_stem(idx_name: str) -> str:
    if not idx_name.endswith(".idx"):
        raise ValueError(f"invalid multi-pack-index pack name: {idx_name!r}")
    return idx_name[:-4]


def _candidate_packs(
    parsed: ParsedMultiPackIndex,
    pack_dir: Path,
    batch_size: int,
) -> Tuple[Tuple[_PackCandidate, ...], int]:
    referenced_counts: Dict[str, int] = {name: 0 for name in parsed.pack_names}
    for entry in parsed.entries:
        referenced_counts[entry.pack_name] += 1

    candidates: List[_PackCandidate] = []
    for idx_name in parsed.pack_names:
        referenced = referenced_counts[idx_name]
        if referenced == 0:
            continue
        stem = _pack_stem(idx_name)
        if (pack_dir / f"{stem}.keep").exists():
            continue

        idx_path = pack_dir / idx_name
        pack_path = idx_path.with_suffix(".pack")
        index = parse_index(idx_path)
        total_objects = len(index.entries)
        if total_objects == 0:
            raise ValueError(f"referenced multi-pack-index pack is empty: {idx_name}")
        pack_size = pack_path.stat().st_size
        expected = (referenced * pack_size) // total_objects
        candidates.append(
            _PackCandidate(
                idx_name=idx_name,
                expected_size=expected,
                mtime_ns=pack_path.stat().st_mtime_ns,
            )
        )

    candidates.sort(key=lambda candidate: (candidate.mtime_ns, candidate.idx_name))
    if batch_size == 0:
        selected = tuple(candidates)
        return selected, sum(candidate.expected_size for candidate in selected)

    selected_list: List[_PackCandidate] = []
    total_expected = 0
    for candidate in candidates:
        # Native Git skips an individual pack whose expected contribution is at
        # least the requested batch size, then keeps considering newer packs.
        if candidate.expected_size >= batch_size:
            continue
        selected_list.append(candidate)
        total_expected += candidate.expected_size
        if total_expected >= batch_size:
            break
    return tuple(selected_list), total_expected


def _read_selected_objects(
    parsed: ParsedMultiPackIndex,
    pack_dir: Path,
    selected_packs: Tuple[str, ...],
) -> Tuple[Tuple[str, object], ...]:
    selected_names = set(selected_packs)
    readers: Dict[str, PackReader] = {}
    objects = []
    for entry in parsed.entries:
        if entry.pack_name not in selected_names:
            continue
        reader = readers.get(entry.pack_name)
        if reader is None:
            reader = PackReader(pack_dir / entry.pack_name)
            readers[entry.pack_name] = reader
        obj = reader.read_object(entry.oid)
        if obj is None:
            raise ValueError(
                f"multi-pack-index source pack no longer contains object {entry.oid}"
            )
        actual = obj.hash()
        if actual != entry.oid:
            raise ValueError(
                f"multi-pack-index source object {entry.oid} re-serializes as {actual}"
            )
        objects.append((entry.oid, obj))
    return tuple(objects)


def _pair_object_set(pack_path: Path, idx_path: Path) -> frozenset[str]:
    pack = parse_pack(pack_path)
    index = parse_index(idx_path)
    pack_meta = {entry.oid: (entry.offset, entry.crc32) for entry in pack.entries}
    index_meta = {entry.oid: (entry.offset, entry.crc32) for entry in index.entries}
    if pack_meta != index_meta:
        raise ValueError(f"pack/index metadata mismatch for {pack_path.name}")
    return frozenset(pack_meta)


def _install_batch_pack(
    repo: Repository,
    objects: Tuple[Tuple[str, object], ...],
) -> Tuple[Path, Path, bool]:
    pack_dir = repo.store.root / "pack"
    expected = {oid for oid, _ in objects}
    if not expected:
        raise ValueError("cannot create an empty multi-pack-index repack")

    with tempfile.TemporaryDirectory(prefix=".midx-repack-", dir=str(pack_dir)) as temp_name:
        temp_dir = Path(temp_name)
        temp_pack, temp_idx = PackWriter(list(objects)).write_pack_and_idx(temp_dir, "pack")
        if set(_pair_object_set(temp_pack, temp_idx)) != expected:
            raise ValueError("generated multi-pack-index repack does not match selection")

        final_pack = pack_dir / temp_pack.name
        final_idx = pack_dir / temp_idx.name
        created = False
        if final_pack.exists() or final_idx.exists():
            if not (final_pack.is_file() and final_idx.is_file()):
                raise RuntimeError(f"incomplete existing MIDX repack target: {final_pack.stem}")
            if (
                final_pack.read_bytes() != temp_pack.read_bytes()
                or final_idx.read_bytes() != temp_idx.read_bytes()
            ):
                raise RuntimeError(f"MIDX repack target collision: {final_pack.stem}")
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
            created = True

        if set(_pair_object_set(final_pack, final_idx)) != expected:
            raise ValueError("installed multi-pack-index repack does not match selection")
        return final_pack, final_idx, created


def _atomic_write(path: Path, data: bytes, suffix: str) -> None:
    temporary = path.with_name(path.name + suffix)
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def _write_preferred_multi_pack_index(pack_dir: Path, preferred_idx: str) -> Path:
    """Rewrite the MIDX while preferring one pack for duplicate object copies."""
    idx_paths = sorted(pack_dir.glob("*.idx"), key=lambda path: path.name)
    pack_names = tuple(path.name for path in idx_paths)
    _encode_pack_names(pack_names)
    if preferred_idx not in pack_names:
        raise ValueError(f"preferred multi-pack-index pack is missing: {preferred_idx}")

    pack_ids = {name: index for index, name in enumerate(pack_names)}
    preferred_path = pack_dir / preferred_idx
    ordered_paths = [preferred_path] + [path for path in idx_paths if path.name != preferred_idx]

    selected: Dict[str, Tuple[int, int]] = {}
    for idx_path in ordered_paths:
        pack_path = idx_path.with_suffix(".pack")
        if not pack_path.is_file():
            raise FileNotFoundError(pack_path)
        index = parse_index(idx_path)
        pack_id = pack_ids[idx_path.name]
        for entry in index.entries:
            selected.setdefault(entry.oid, (pack_id, entry.offset))

    data = _build_bytes(pack_names, selected)
    output = pack_dir / "multi-pack-index"
    _atomic_write(output, data, ".repack.tmp")
    return output


def repack_multi_pack_index(
    repo: Repository,
    path: Path,
    *,
    batch_size: int = 0,
) -> MultiPackIndexRepackResult:
    """Create one verified batch pack and make the MIDX prefer it.

    ``batch_size=0`` selects every non-kept pack referenced by the current MIDX.
    For a positive size, packs are considered oldest-to-newest using Git's
    expected-size heuristic. Selecting fewer than two packs is a no-op.

    The source MIDX is verified before mutation. Selected objects are read from
    the exact MIDX-selected source packs through ``PackReader`` so loose-object
    state cannot redirect or weaken verification. Old packs are intentionally
    retained; a later ``multi-pack-index expire`` removes those that became
    fully redundant after the new preferred pack was indexed.
    """
    if batch_size < 0:
        raise ValueError("multi-pack-index repack batch size must be non-negative")

    path = Path(path)
    parsed = verify_multi_pack_index(path)
    pack_dir = path.parent
    selected_candidates, expected_size = _candidate_packs(parsed, pack_dir, batch_size)
    selected_packs = tuple(candidate.idx_name for candidate in selected_candidates)

    if len(selected_packs) < 2:
        return MultiPackIndexRepackResult(
            path=path,
            batch_size=batch_size,
            selected_packs=selected_packs,
            expected_size=expected_size,
            object_count=0,
            pack_path=None,
            idx_path=None,
        )

    objects = _read_selected_objects(parsed, pack_dir, selected_packs)
    if not objects:
        raise ValueError("multi-pack-index repack selected packs but no objects")

    original_midx = path.read_bytes()
    final_pack: Optional[Path] = None
    final_idx: Optional[Path] = None
    created = False
    try:
        final_pack, final_idx, created = _install_batch_pack(repo, objects)
        rewritten = _write_preferred_multi_pack_index(pack_dir, final_idx.name)
        verified = verify_multi_pack_index(rewritten)
        for oid, _ in objects:
            entry = verified.lookup(oid)
            if entry is None or entry.pack_name != final_idx.name:
                raise ValueError(
                    f"rewritten multi-pack-index did not prefer repacked object {oid}"
                )
    except Exception:
        _atomic_write(path, original_midx, ".rollback.tmp")
        if created and final_idx is not None and final_pack is not None:
            try:
                final_idx.unlink()
            except FileNotFoundError:
                pass
            try:
                final_pack.unlink()
            except FileNotFoundError:
                pass
        raise

    assert final_pack is not None and final_idx is not None
    return MultiPackIndexRepackResult(
        path=path,
        batch_size=batch_size,
        selected_packs=selected_packs,
        expected_size=expected_size,
        object_count=len(objects),
        pack_path=final_pack,
        idx_path=final_idx,
    )
