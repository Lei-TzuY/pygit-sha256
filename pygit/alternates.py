"""Alternate object-database discovery for pygit's SHA-256 store.

Git-compatible repository layout permits an object database to borrow objects
from other object directories listed in ``objects/info/alternates``. Pygit uses
the same file location and relative-path rule while keeping its own SHA-256
object format: every non-empty line names another *pygit* object folder, and
relative paths are resolved from the object database itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


_MAX_ALTERNATE_DEPTH = 64


def _direct_alternates(objects_dir: Path) -> Tuple[Path, ...]:
    """Return validated direct alternates for one object database."""
    root = Path(objects_dir).resolve()
    path = root / "info" / "alternates"
    if not path.exists():
        return ()
    if not path.is_file():
        raise ValueError(f"alternates path is not a file: {path}")

    text = path.read_bytes().decode("utf-8", "surrogateescape")
    if "\x00" in text:
        raise ValueError(f"alternates file contains NUL: {path}")

    result: List[Path] = []
    seen = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw == "":
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not candidate.is_dir():
            raise FileNotFoundError(
                f"alternate object directory from {path}:{lineno} does not exist: {candidate}"
            )
        if candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return tuple(result)


def alternate_object_dirs(
    objects_dir: Path,
    *,
    max_depth: int = _MAX_ALTERNATE_DEPTH,
) -> Tuple[Path, ...]:
    """Return transitive alternate object databases in deterministic DFS order.

    Repeated paths and cycles are suppressed by canonical resolved path. A
    finite depth bound protects against accidentally enormous metadata chains.
    """
    if max_depth < 1:
        raise ValueError("max alternate depth must be positive")

    primary = Path(objects_dir).resolve()
    seen = {primary}
    result: List[Path] = []

    def visit(root: Path, depth: int) -> None:
        if depth >= max_depth:
            direct = _direct_alternates(root)
            if any(path not in seen for path in direct):
                raise ValueError(
                    f"alternate object database chain exceeds depth {max_depth}"
                )
            return

        for candidate in _direct_alternates(root):
            if candidate in seen:
                continue
            seen.add(candidate)
            result.append(candidate)
            visit(candidate, depth + 1)

    visit(primary, 0)
    return tuple(result)


__all__ = ["alternate_object_dirs"]
