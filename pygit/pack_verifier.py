"""Strict integrity verifier for pygit's educational SHA-256 pack pairs.

``verify_packfile`` deliberately reuses the canonical pack-index and pack
parsers instead of maintaining a third binary decoder. The complete ``.idx``
and ``.pack`` images are validated first, then their object records are
cross-checked for identity, CRC32, and byte offsets.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from .pack_index import parse_index
from .pack_plumbing import parse_pack


VerifyPackRecord = Tuple[str, str, int, int, int]


def verify_packfile(
    idx_path: Path,
    verbose: bool = False,
) -> List[VerifyPackRecord]:
    """Validate one ``.idx``/``.pack`` pair and return object metadata.

    The return shape is retained for compatibility with the existing
    ``Repository.verify_pack`` API::

        (oid, type_name, size, compressed_size, offset)

    ``verbose`` is retained as an API compatibility parameter; verification is
    always strict regardless of its value.
    """
    del verbose

    idx_path = Path(idx_path)
    pack_path = idx_path.with_suffix(".pack")
    if not idx_path.is_file() or not pack_path.is_file():
        raise FileNotFoundError(f"Packfile or index file missing for: {idx_path}")

    index = parse_index(idx_path)
    pack = parse_pack(pack_path)

    if index.object_count != len(pack.entries):
        raise ValueError(
            "pack/index object count mismatch: "
            f"index has {index.object_count}, pack has {len(pack.entries)}"
        )

    pack_by_oid = {entry.oid: entry for entry in pack.entries}
    index_oids = {entry.oid for entry in index.entries}
    pack_oids = set(pack_by_oid)
    if index_oids != pack_oids:
        missing = sorted(pack_oids - index_oids)
        extra = sorted(index_oids - pack_oids)
        details = []
        if missing:
            details.append(f"missing from index: {', '.join(missing)}")
        if extra:
            details.append(f"not present in pack: {', '.join(extra)}")
        raise ValueError("pack/index object ID mismatch: " + "; ".join(details))

    results: List[VerifyPackRecord] = []
    for indexed in index.entries:
        packed = pack_by_oid[indexed.oid]
        if indexed.offset != packed.offset:
            raise ValueError(
                f"pack/index offset mismatch for object {indexed.oid}: "
                f"index has {indexed.offset}, pack has {packed.offset}"
            )
        if indexed.crc32 != packed.crc32:
            raise ValueError(
                f"CRC-32 mismatch for object {indexed.oid} at offset {indexed.offset}"
            )
        results.append(
            (
                indexed.oid,
                packed.type_name,
                packed.size,
                packed.compressed_size,
                packed.offset,
            )
        )

    return results
