"""
pygit/pack_verifier.py
======================
Packfile & Fan-out Index Integrity Verifier
===========================================

Validates CRC-32 checksums, offsets, and object stream decompression for .idx / .pack pairs.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Dict, List, Tuple

from .pack import PackReader, _ID_TYPE_MAP


def verify_packfile(idx_path: Path, verbose: bool = False) -> List[Tuple[str, str, int, int, int]]:
    """
    Verify CRC-32 and offsets in *idx_path*.

    Returns a list of ``(sha, type_name, size, compressed_size, offset)``.
    """
    pack_path = idx_path.with_suffix(".pack")
    if not idx_path.exists() or not pack_path.exists():
        raise FileNotFoundError(f"Packfile or index file missing for: {idx_path}")

    idx_bytes = idx_path.read_bytes()
    pack_bytes = pack_path.read_bytes()

    if len(idx_bytes) < 1032 or not idx_bytes.startswith(b"\xfftOc"):
        raise ValueError("Invalid index file header.")

    total_objs = struct.unpack(">I", idx_bytes[1028:1032])[0]
    pos = 1032

    # Read SHAs
    shas = []
    for _ in range(total_objs):
        sha_str = idx_bytes[pos : pos + 64].decode("utf-8")
        shas.append(sha_str)
        pos += 64

    # Read CRCs
    crcs = []
    for _ in range(total_objs):
        crc = struct.unpack(">I", idx_bytes[pos : pos + 4])[0]
        crcs.append(crc)
        pos += 4

    # Read Offsets
    offsets = []
    for _ in range(total_objs):
        off = struct.unpack(">I", idx_bytes[pos : pos + 4])[0]
        offsets.append(off)
        pos += 4

    results: List[Tuple[str, str, int, int, int]] = []

    for i in range(total_objs):
        sha = shas[i]
        expected_crc = crcs[i]
        offset = offsets[i]

        p = offset
        first = pack_bytes[p]
        type_id = (first >> 4) & 0x07
        size = first & 0x0F
        shift = 4
        p += 1
        while first & 0x80:
            first = pack_bytes[p]
            size |= (first & 0x7F) << shift
            shift += 7
            p += 1

        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(pack_bytes[p:])
        entry_len = (p - offset) + len(pack_bytes[p:]) - len(decompressor.unconsumed_tail) - len(decompressor.unused_data)
        entry_bytes = pack_bytes[offset : offset + entry_len]

        actual_crc = zlib.crc32(entry_bytes) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"CRC-32 mismatch for object {sha} at offset {offset}")

        type_name = _ID_TYPE_MAP.get(type_id, b"blob").decode("utf-8")
        results.append((sha, type_name, size, entry_len, offset))

    return results
