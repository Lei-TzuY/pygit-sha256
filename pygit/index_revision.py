"""Git-style index object expressions for the shared revision resolver.

The readable pygit index currently stores one stage-0 entry per path.  This
module implements ``:<path>`` and ``:0:<path>`` without pretending that the
JSON format can represent Git's unmerged stages 1-3.
"""

from __future__ import annotations

import os
from pathlib import Path

from .repo import Repository


def _normalize_cwd_relative_path(repo: Repository, path: str) -> str:
    """Resolve a leading ``./`` or ``../`` path relative to the current cwd."""

    root = Path(os.path.abspath(str(repo.worktree)))
    cwd = Path(os.path.abspath(str(Path.cwd())))
    try:
        cwd.relative_to(root)
    except ValueError as exc:
        raise ValueError("current directory is outside the repository") from exc

    candidate = Path(os.path.abspath(str(cwd / path)))
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"index path is outside the repository: {path!r}") from exc
    if not relative or relative == ".":
        raise KeyError("index expression does not name a path")
    return relative


def parse_index_expression(repo: Repository, expression: str) -> tuple[int, str]:
    """Return ``(stage, path)`` for one Git-style ``:<path>`` expression.

    Git only treats ``:0:`` through ``:3:`` as stage prefixes.  Other text after
    the first colon is part of the path, so an index entry named ``4:a`` is
    addressed as ``:4:a`` rather than being parsed as an invalid stage.
    """

    if not expression.startswith(":"):
        raise ValueError(f"not an index expression: {expression!r}")

    stage = 0
    path = expression[1:]
    if len(expression) >= 3 and expression[1] in "0123" and expression[2] == ":":
        stage = int(expression[1])
        path = expression[3:]

    if not path or "\x00" in path:
        raise KeyError(f"index expression does not name a path: {expression!r}")
    if stage != 0:
        raise ValueError(
            f"index stage {stage} is not supported by pygit's stage-0 JSON index"
        )

    if path.startswith("./") or path.startswith("../"):
        path = _normalize_cwd_relative_path(repo, path)
    return stage, path


def resolve_index_expression(repo: Repository, expression: str) -> str:
    """Resolve ``:<path>`` / ``:0:<path>`` to the staged object ID."""

    _, path = parse_index_expression(repo, expression)
    entry = repo.index.get(path)
    if entry is None:
        raise KeyError(f"Path {path!r} is not in the index")

    oid = entry.sha.lower()
    if not repo.store.exists(oid):
        raise KeyError(f"Index path {path!r} names missing object {oid}")
    return oid
