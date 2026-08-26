"""Selective multi-pack-index writes driven by ``write --stdin-packs`` records."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

from .multi_pack_index import (
    _normalize_preferred_pack_name,
    _validate_pack_name,
    parse_multi_pack_index_bytes,
    write_multi_pack_index,
)


@dataclass(frozen=True)
class MultiPackIndexStdinWriteResult:
    """Summary of one successful selective MIDX write."""

    path: Path
    pack_names: Tuple[str, ...]
    ignored_preferred_pack: Optional[str] = None


def _selected_pack_names(pack_dir: Path, records: Iterable[str]) -> Tuple[str, ...]:
    """Return existing canonical ``.idx`` basenames selected by stdin records.

    Git's ``--stdin-packs`` treats each input line as an exact pack-index
    basename. Blank, malformed, missing, and duplicate records do not add a
    pack to the selected set; surrounding whitespace is intentionally not
    stripped because it is part of the record name.
    """

    selected = set()
    for raw in records:
        name = raw.rstrip("\r\n")
        if not name:
            continue
        try:
            _validate_pack_name(name)
        except ValueError:
            continue
        if (pack_dir / name).is_file():
            selected.add(name)
    return tuple(sorted(selected))


def write_multi_pack_index_from_packs(
    pack_dir: Path,
    records: Iterable[str],
    *,
    preferred_pack: Optional[str] = None,
) -> MultiPackIndexStdinWriteResult:
    """Write a MIDX containing only pack indexes named by *records*.

    The existing Phase 108 writer remains the single implementation of
    duplicate-copy ownership. A temporary staging directory contains only the
    selected indexes plus lightweight pack placeholders with the real pack
    mtimes, so the ordinary writer naturally applies the same preferred-pack,
    oldest-pack, newest-copy, and basename tie-break semantics to the reduced
    pack universe.

    An explicit preferred pack that is not in the selected set is ignored, as
    native Git does for ``--stdin-packs``; callers can surface
    ``ignored_preferred_pack`` as a warning. Missing selected packfiles and
    malformed selected indexes remain fatal before the destination MIDX is
    replaced.
    """

    pack_dir = Path(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack_names = _selected_pack_names(pack_dir, records)
    if not pack_names:
        raise ValueError("cannot write multi-pack-index without selected pack indexes")

    source_pairs = []
    for idx_name in pack_names:
        idx_path = pack_dir / idx_name
        pack_path = idx_path.with_suffix(".pack")
        if not pack_path.is_file():
            raise FileNotFoundError(pack_path)
        source_pairs.append((idx_path, pack_path))

    ignored_preferred: Optional[str] = None
    staged_preferred = preferred_pack
    if preferred_pack is not None:
        preferred_idx = _normalize_preferred_pack_name(preferred_pack)
        if preferred_idx not in pack_names:
            ignored_preferred = preferred_pack
            staged_preferred = None

    with tempfile.TemporaryDirectory(prefix=".midx-stdin-", dir=str(pack_dir)) as temp_name:
        staging = Path(temp_name)
        for idx_path, pack_path in source_pairs:
            shutil.copy2(idx_path, staging / idx_path.name)
            placeholder = staging / pack_path.name
            placeholder.touch()
            stat = pack_path.stat()
            os.utime(
                placeholder,
                ns=(stat.st_atime_ns, stat.st_mtime_ns),
            )

        staged_path = write_multi_pack_index(
            staging,
            preferred_pack=staged_preferred,
        )
        data = staged_path.read_bytes()
        parsed = parse_multi_pack_index_bytes(data)
        if parsed.pack_names != pack_names:
            raise RuntimeError("selective multi-pack-index write changed the selected pack set")

    output = pack_dir / "multi-pack-index"
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(str(temporary), str(output))
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise

    return MultiPackIndexStdinWriteResult(
        path=output,
        pack_names=pack_names,
        ignored_preferred_pack=ignored_preferred,
    )
