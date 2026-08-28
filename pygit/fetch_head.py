"""Read and write Git-style FETCH_HEAD metadata for SHA-256-native fetches."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional, Sequence


_HEX = frozenset("0123456789abcdef")


def _description(refname: str, source: str) -> str:
    if refname.startswith("refs/heads/"):
        return f"branch '{refname[len('refs/heads/'): ]}' of {source}"
    if refname.startswith("refs/tags/"):
        return f"tag '{refname[len('refs/tags/'): ]}' of {source}"
    return f"'{refname}' of {source}"


def read_fetch_head_oid(pygit_dir: Path) -> Optional[str]:
    """Return the object ID named by the first ``FETCH_HEAD`` entry.

    Git treats ``FETCH_HEAD`` as a pseudo-ref whose revision value is the first
    object recorded by the most recent fetch.  The merge/not-for-merge marker
    affects pull/merge selection, not pseudo-ref resolution itself.

    pygit's repository-native object IDs are SHA-256, so a valid entry must
    begin with one 64-hex object ID.  Missing or empty metadata simply means
    the pseudo-ref is unresolved; malformed metadata is reported explicitly.
    """
    path = pygit_dir / "FETCH_HEAD"
    if not path.exists():
        return None

    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        oid = raw.split(None, 1)[0].lower()
        if len(oid) != 64 or any(char not in _HEX for char in oid):
            raise RuntimeError("Malformed FETCH_HEAD: expected a 64-hex object ID")
        return oid
    return None


def write_fetch_head(
    pygit_dir: Path,
    refs: Mapping[str, str],
    *,
    source: str,
    mergeable: Sequence[str] = (),
    append: bool = False,
) -> None:
    """Write fetched SHA-256 object names and source refs to ``FETCH_HEAD``.

    Git stores object IDs in the repository's native object format. pygit's
    repository format is SHA-256-native, so these entries intentionally contain
    64-hex pygit object IDs rather than transport-side SHA-1 IDs.
    """
    mergeable_set = set(mergeable)
    lines = []
    for refname, oid in refs.items():
        marker = "" if refname in mergeable_set else "not-for-merge"
        lines.append(f"{oid}\t{marker}\t{_description(refname, source)}\n")

    path = pygit_dir / "FETCH_HEAD"
    mode = "a" if append else "w"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as fh:
        fh.writelines(lines)
