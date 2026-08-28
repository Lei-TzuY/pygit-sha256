"""Helpers for Git-style fetch prefetch namespace routing."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .fetch_policy import FetchRefspec
from .packed_refs import list_packed_refnames, remove_packed_refs


PREFIX = "refs/prefetch/"


def prefetch_refspec(spec: FetchRefspec) -> FetchRefspec:
    """Rewrite only a configured refspec destination into refs/prefetch/."""
    if spec.negative or spec.destination is None:
        return spec
    destination = spec.destination
    if not destination.startswith("refs/"):
        raise ValueError(f"invalid fetch destination for prefetch: {destination!r}")
    rewritten = PREFIX + destination[len("refs/") :]
    return FetchRefspec(
        raw=spec.raw,
        source=spec.source,
        destination=rewritten,
        force=spec.force,
        negative=spec.negative,
    )


def prefetch_refspecs(specs: Iterable[FetchRefspec]) -> List[FetchRefspec]:
    return [prefetch_refspec(spec) for spec in specs]


def _prefetch_path(repo, refname: str) -> Path:
    if not refname.startswith(PREFIX):
        raise ValueError(f"expected prefetch ref: {refname!r}")
    relative = refname[len("refs/") :]
    root = repo.pygit_dir / "refs"
    path = root.joinpath(*relative.split("/"))
    resolved_root = root.resolve()
    resolved_parent = path.parent.resolve()
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise ValueError(f"invalid prefetch ref path: {refname!r}")
    return path


def set_prefetch_ref(repo, refname: str, sha: str) -> None:
    if len(sha) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in sha):
        raise ValueError("prefetch refs require a 64-hex SHA-256 object ID")
    path = _prefetch_path(repo, refname)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sha.lower(), encoding="utf-8")


def list_prefetch_refs(repo) -> List[str]:
    root = repo.pygit_dir / "refs" / "prefetch"
    loose = set()
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                loose.add("refs/prefetch/" + path.relative_to(root).as_posix())
    packed = set(list_packed_refnames(repo.pygit_dir, PREFIX))
    return sorted(loose | packed)


def delete_prefetch_ref(repo, refname: str) -> None:
    path = _prefetch_path(repo, refname)
    if path.exists():
        path.unlink()
        root = repo.pygit_dir / "refs" / "prefetch"
        parent = path.parent
        while parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
    remove_packed_refs(repo.pygit_dir, [refname])
