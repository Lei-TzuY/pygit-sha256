"""
pygit/diagnostics.py
====================
Repository Object Counter & Diagnostic Reporter
===============================================

Calculates loose object count, packed object count, disk space (KB), and packfile statistics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from .pack import PackReader


def analyze_repository_objects(pygit_dir: Path) -> Dict[str, int]:
    """
    Analyze loose objects, packfiles, disk space (KB), and packed object counts.
    """
    objects_dir = pygit_dir / "objects"
    loose_count = 0
    loose_size_bytes = 0

    if objects_dir.exists():
        for d in objects_dir.iterdir():
            if d.is_dir() and len(d.name) == 2 and d.name != "pack" and d.name != "info":
                for f in d.iterdir():
                    if f.is_file():
                        loose_count += 1
                        loose_size_bytes += f.stat().st_size

    pack_dir = objects_dir / "pack"
    packed_count = 0
    pack_files_count = 0
    pack_size_bytes = 0

    if pack_dir.exists():
        for idx_path in pack_dir.glob("*.idx"):
            pack_files_count += 1
            reader = PackReader(idx_path)
            packed_count += len(reader.get_shas())
            pack_size_bytes += idx_path.stat().st_size
            pack_file = idx_path.with_suffix(".pack")
            if pack_file.exists():
                pack_size_bytes += pack_file.stat().st_size

    return {
        "count": loose_count,
        "size_kb": (loose_size_bytes + 1023) // 1024,
        "in_pack": packed_count,
        "packs": pack_files_count,
        "size_pack_kb": (pack_size_bytes + 1023) // 1024,
    }
