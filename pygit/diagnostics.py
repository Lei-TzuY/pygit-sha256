"""
pygit/diagnostics.py
====================
Repository object counter and storage diagnostics.

The scanner deliberately works from the object database layout rather than
``ObjectStore.all_shas()`` so it can distinguish valid loose/packed objects
from garbage and report loose objects that are redundant with a pack.
"""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path
from typing import Dict, List, Set

from .pack import PackReader
from .store import ObjectStore


_HEX = frozenset("0123456789abcdef")


def _is_hex(value: str) -> bool:
    return bool(value) and all(char in _HEX for char in value.lower())


def _valid_loose_object(path: Path, oid: str) -> bool:
    """Validate one canonical loose-object candidate without pack fallback."""

    try:
        store_bytes = zlib.decompress(path.read_bytes())
        if hashlib.sha256(store_bytes).hexdigest() != oid:
            return False
        ObjectStore._parse(store_bytes)
    except (OSError, EOFError, zlib.error, ValueError, IndexError, UnicodeError):
        return False
    return True


def _alternates(objects_dir: Path) -> List[str]:
    path = objects_dir / "info" / "alternates"
    if not path.is_file():
        return []
    result: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (objects_dir / candidate).resolve()
        result.append(str(candidate))
    return result


def analyze_repository_objects(pygit_dir: Path) -> Dict[str, object]:
    """Return Git-style loose/pack/garbage storage diagnostics.

    Valid loose objects must live at ``objects/HH/<62 hex>`` and pass zlib,
    SHA-256, envelope, and typed-payload validation. Pack statistics count only
    validated ``.idx``/``.pack`` pairs. Invalid or orphan storage files are
    reported as garbage instead of being silently counted as objects.
    """

    objects_dir = pygit_dir / "objects"
    loose_oids: Set[str] = set()
    loose_size_bytes = 0
    garbage_count = 0
    garbage_size_bytes = 0

    if objects_dir.is_dir():
        for entry in objects_dir.iterdir():
            if entry.name in {"pack", "info"}:
                continue
            if entry.is_dir() and len(entry.name) == 2 and _is_hex(entry.name):
                for child in entry.iterdir():
                    if not child.is_file():
                        continue
                    oid = (entry.name + child.name).lower()
                    if len(child.name) == 62 and _is_hex(child.name) and _valid_loose_object(child, oid):
                        loose_oids.add(oid)
                        loose_size_bytes += child.stat().st_size
                    else:
                        garbage_count += 1
                        garbage_size_bytes += child.stat().st_size
                continue
            if entry.is_file():
                garbage_count += 1
                garbage_size_bytes += entry.stat().st_size

    pack_dir = objects_dir / "pack"
    packed_oids: Set[str] = set()
    packed_count = 0
    pack_files_count = 0
    pack_size_bytes = 0

    if pack_dir.is_dir():
        idx_by_stem = {path.stem: path for path in pack_dir.glob("*.idx") if path.is_file()}
        pack_by_stem = {path.stem: path for path in pack_dir.glob("*.pack") if path.is_file()}
        valid_stems: Set[str] = set()

        for stem in sorted(set(idx_by_stem) | set(pack_by_stem)):
            idx_path = idx_by_stem.get(stem)
            pack_path = pack_by_stem.get(stem)
            if idx_path is None or pack_path is None:
                orphan = idx_path or pack_path
                assert orphan is not None
                garbage_count += 1
                garbage_size_bytes += orphan.stat().st_size
                continue

            try:
                reader = PackReader(idx_path)
                reader._load_pack_image()
                shas = reader.get_shas()
            except (OSError, EOFError, ValueError, RuntimeError, KeyError, zlib.error):
                garbage_count += 2
                garbage_size_bytes += idx_path.stat().st_size + pack_path.stat().st_size
                continue

            valid_stems.add(stem)
            pack_files_count += 1
            packed_count += len(shas)
            packed_oids.update(shas)
            pack_size_bytes += idx_path.stat().st_size + pack_path.stat().st_size

        for path in pack_dir.iterdir():
            if not path.is_file() or path.suffix in {".idx", ".pack"}:
                continue
            # A small set of standard sidecar files is metadata rather than
            # garbage when it belongs to a validated pack.
            if path.stem in valid_stems and path.suffix in {".keep", ".bitmap", ".rev"}:
                continue
            garbage_count += 1
            garbage_size_bytes += path.stat().st_size

    return {
        "count": len(loose_oids),
        "size_kb": (loose_size_bytes + 1023) // 1024,
        "size_bytes": loose_size_bytes,
        "in_pack": packed_count,
        "packs": pack_files_count,
        "size_pack_kb": (pack_size_bytes + 1023) // 1024,
        "size_pack_bytes": pack_size_bytes,
        "prune_packable": len(loose_oids & packed_oids),
        "garbage": garbage_count,
        "size_garbage_kb": (garbage_size_bytes + 1023) // 1024,
        "size_garbage_bytes": garbage_size_bytes,
        "alternates": _alternates(objects_dir),
    }
