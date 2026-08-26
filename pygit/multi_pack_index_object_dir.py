"""Alternate object-directory routing for ``pygit multi-pack-index``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from .repo import Repository


def configured_alternates(repo: Repository) -> Tuple[Path, ...]:
    """Return canonical object directories listed by ``objects/info/alternates``."""
    object_dir = repo.pygit_dir / "objects"
    alternates = object_dir / "info" / "alternates"
    if not alternates.is_file():
        return ()

    resolved = []
    seen = set()
    for raw in alternates.read_text(encoding="utf-8").splitlines():
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            # Git defines relative alternate paths relative to the object database.
            path = object_dir / path
        canonical = path.resolve()
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)
    return tuple(resolved)


def resolve_multi_pack_index_object_dir(
    repo: Repository,
    requested: Optional[str],
) -> Path:
    """Resolve and validate the object directory used by MIDX maintenance.

    The repository's own object directory is the default. An explicit directory
    must be one of the repository's configured alternates, matching Git's
    ``multi-pack-index --object-dir`` safety boundary.
    """
    primary = (repo.pygit_dir / "objects").resolve()
    if requested is None:
        return primary

    candidate = Path(requested).resolve()
    if candidate == primary:
        return primary

    if candidate not in configured_alternates(repo):
        raise ValueError(
            f"multi-pack-index object directory is not a configured alternate: {candidate}"
        )
    if not candidate.is_dir():
        raise ValueError(f"multi-pack-index object directory does not exist: {candidate}")
    return candidate
