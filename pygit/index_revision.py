"""Git-style index object expressions for the shared revision resolver.

Phase 118 introduced ``:<path>`` / ``:0:<path>`` for the readable stage-0
index. Phase 122 extends the same grammar to real conflict stages 1-3 stored by
the multi-stage index backend.
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

    Git only treats ``:0:`` through ``:3:`` as stage prefixes. Other text after
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
    if path.startswith("./") or path.startswith("../"):
        path = _normalize_cwd_relative_path(repo, path)
    return stage, path


def resolve_index_expression(repo: Repository, expression: str) -> str:
    """Resolve a stage-aware index expression to its object ID."""

    stage, path = parse_index_expression(repo, expression)
    entry = repo.index.get(path, stage)
    if entry is None:
        raise KeyError(f"Path {path!r} has no index stage {stage}")

    oid = entry.sha.lower()
    if not repo.store.exists(oid):
        raise KeyError(
            f"Index path {path!r} stage {stage} names missing object {oid}"
        )
    return oid
