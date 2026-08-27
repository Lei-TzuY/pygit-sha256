"""Write Git-style FETCH_HEAD metadata for SHA-256-native fetches."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


def _description(refname: str, source: str) -> str:
    if refname.startswith("refs/heads/"):
        return f"branch '{refname[len('refs/heads/'): ]}' of {source}"
    if refname.startswith("refs/tags/"):
        return f"tag '{refname[len('refs/tags/'): ]}' of {source}"
    return f"'{refname}' of {source}"


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
