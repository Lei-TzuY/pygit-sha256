"""Validation and inspection helpers for pygit's educational pack format."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from .pack import PackReader
from .pack_index import ParsedPackIndex, parse_index


@dataclass(frozen=True)
class VerifyPackObject:
    """One fully verified non-delta packed object."""

    oid: str
    type_name: str
    size: int
    packed_size: int
    offset: int


@dataclass(frozen=True)
class VerifyPackResult:
    """Validation result for one paired ``.idx`` / ``.pack`` archive."""

    idx_path: Path
    pack_path: Path
    index: ParsedPackIndex
    objects: Tuple[VerifyPackObject, ...]

    @property
    def object_count(self) -> int:
        return len(self.index.entries)


def verify_pack(idx_path: Path) -> VerifyPackResult:
    """Fully validate one index and every object in its corresponding pack.

    The shared strict index parser validates index signature/version/fan-out,
    canonical SHA-256 ordering, offsets, and the index checksum. ``PackReader``
    then validates the pack envelope and every indexed object, including pack
    checksum/count/offset bounds, bounded decompression, CRC-32, object envelope,
    type, and recomputed SHA-256 object identity.
    """

    idx_path = Path(idx_path)
    if idx_path.suffix != ".idx":
        raise ValueError(f"verify-pack expects an .idx file: {idx_path}")

    index = parse_index(idx_path)
    reader = PackReader(idx_path)
    pack_bytes, payload_end = reader._load_pack_image()

    objects = []
    for entry in index.entries:
        obj = reader.read_object(entry.oid)
        if obj is None:
            raise RuntimeError(f"indexed object disappeared during verification: {entry.oid}")
        entry_end = reader._entry_end(entry.offset, payload_end)
        if entry_end <= entry.offset:
            raise ValueError(f"invalid pack entry boundary for object {entry.oid}")
        if entry_end > len(pack_bytes) - 32:
            raise ValueError(f"pack entry for object {entry.oid} overlaps pack checksum")
        objects.append(
            VerifyPackObject(
                oid=entry.oid,
                type_name=obj.type_name.decode("ascii"),
                size=len(obj.serialize()),
                packed_size=entry_end - entry.offset,
                offset=entry.offset,
            )
        )

    return VerifyPackResult(
        idx_path=idx_path,
        pack_path=idx_path.with_suffix(".pack"),
        index=index,
        objects=tuple(objects),
    )
