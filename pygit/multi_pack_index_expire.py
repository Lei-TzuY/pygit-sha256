"""Lifecycle cleanup for redundant packs tracked by a multi-pack-index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from .multi_pack_index import verify_multi_pack_index, write_multi_pack_index


_PACK_SIDECARS = (".pack", ".idx", ".rev", ".bitmap")


@dataclass(frozen=True)
class MultiPackIndexExpireResult:
    """Summary of one successful multi-pack-index expiration pass."""

    path: Path
    expired_packs: Tuple[str, ...]
    kept_packs: Tuple[str, ...]

    @property
    def expired_count(self) -> int:
        return len(self.expired_packs)


def _pack_stem(idx_name: str) -> str:
    if not idx_name.endswith(".idx"):
        raise ValueError(f"invalid multi-pack-index pack name: {idx_name!r}")
    return idx_name[:-4]


def expire_multi_pack_index(path: Path) -> MultiPackIndexExpireResult:
    """Delete fully redundant MIDX-tracked packs and rewrite the MIDX.

    A pack is eligible only when no object entry in the *verified* current MIDX
    references that pack. Packs with a sibling ``.keep`` marker are never
    removed. Pygit has no cruft-pack format, so there is no separate cruft
    exception to model.

    The existing MIDX and every tracked pack/index pair are verified before any
    deletion. After cleanup, the MIDX is rebuilt from the remaining indexes and
    verified again. Known generated sidecars (``.rev`` and ``.bitmap``) are
    removed together with the redundant ``.pack``/``.idx`` pair.
    """

    path = Path(path)
    parsed = verify_multi_pack_index(path)
    pack_dir = path.parent
    referenced = {entry.pack_name for entry in parsed.entries}

    candidates = []
    for idx_name in parsed.pack_names:
        if idx_name in referenced:
            continue
        stem = _pack_stem(idx_name)
        if (pack_dir / f"{stem}.keep").exists():
            continue
        candidates.append(idx_name)

    if not candidates:
        return MultiPackIndexExpireResult(
            path=path,
            expired_packs=(),
            kept_packs=parsed.pack_names,
        )

    # A non-empty MIDX always references at least one pack. Refuse to remove the
    # final index defensively rather than depending on write-time failure.
    survivors = [name for name in parsed.pack_names if name not in candidates]
    if not survivors:
        raise ValueError("multi-pack-index expire would remove every tracked pack")

    for idx_name in candidates:
        stem = _pack_stem(idx_name)
        for suffix in _PACK_SIDECARS:
            sidecar = pack_dir / f"{stem}{suffix}"
            try:
                sidecar.unlink()
            except FileNotFoundError:
                if suffix in {".pack", ".idx"}:
                    raise

    rewritten = write_multi_pack_index(pack_dir)
    verified = verify_multi_pack_index(rewritten)
    return MultiPackIndexExpireResult(
        path=rewritten,
        expired_packs=tuple(candidates),
        kept_packs=verified.pack_names,
    )
