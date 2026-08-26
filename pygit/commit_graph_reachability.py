"""Reachability selection for ``commit-graph write``.

The binary commit-graph codec deliberately knows nothing about repository refs.
This module supplies the repository-facing selection layer: normal writes cover
all commit-ish refs plus HEAD, while script callers may provide an explicit set
of commit-ish roots (for example through ``--stdin-commits``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from .commit_graph import CommitGraph
from .objects import CommitObject
from .plumbing import list_refs
from .rev_list import rev_list

if TYPE_CHECKING:
    from .repo import Repository


CommitGraphInput = Tuple[str, str, List[str]]


def collect_commit_graph_commits(
    repo: "Repository",
    revisions: Optional[Sequence[str]] = None,
) -> List[CommitGraphInput]:
    """Return deterministic commit-graph input for the requested roots.

    ``revisions is None`` is the repository-wide mode used by the normal CLI:
    every commit-ish ref participates and HEAD is included independently so a
    detached HEAD cannot disappear merely because local branches also exist.

    An explicit ``revisions`` sequence selects only those commit-ish roots and
    their ancestry. Empty explicit input is rejected instead of silently
    falling back to repository-wide behavior.
    """
    if revisions is None:
        head = repo.refs.resolve_head()
        refs = list_refs(repo)
        if head is None and not refs:
            entries = ()
        else:
            roots = [head] if head is not None else []
            entries = rev_list(repo, roots, all_refs=True, topo_order=True)
    else:
        roots = [revision.strip() for revision in revisions if revision.strip()]
        if not roots:
            raise ValueError("commit-graph explicit root set is empty")
        entries = rev_list(repo, roots, topo_order=True)

    result: List[CommitGraphInput] = []
    for entry in entries:
        obj = repo.store.read(entry.oid)
        if not isinstance(obj, CommitObject):
            raise RuntimeError(
                f"commit-graph traversal selected non-commit object {entry.oid}"
            )
        result.append((entry.oid, obj.tree, list(obj.parents)))
    return result


def write_reachable_commit_graph(
    repo: "Repository",
    revisions: Optional[Sequence[str]] = None,
):
    """Atomically write a graph for repository-wide or explicit reachability."""
    commits = collect_commit_graph_commits(repo, revisions)
    return CommitGraph(repo.pygit_dir).write(commits)
