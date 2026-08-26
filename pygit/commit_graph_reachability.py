"""Reachability selection and coverage checks for commit-graph maintenance.

The binary commit-graph codec deliberately knows nothing about repository refs.
This module supplies the repository-facing selection layer: normal writes cover
all commit-ish refs plus HEAD, while script callers may provide an explicit set
of commit-ish roots (for example through ``--stdin-commits``). Coverage checks
reuse the same traversal so verification cannot silently drift from write
selection semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

from .commit_graph import CommitGraph, CommitGraphError
from .objects import CommitObject
from .plumbing import list_refs
from .rev_list import rev_list

if TYPE_CHECKING:
    from .repo import Repository


CommitGraphInput = Tuple[str, str, List[str]]


@dataclass(frozen=True)
class CommitGraphCoverage:
    """Summary of one successful reachability-coverage verification."""

    expected_count: int
    indexed_count: int
    extra_count: int


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


def verify_commit_graph_coverage(
    repo: "Repository",
    revisions: Optional[Sequence[str]] = None,
) -> CommitGraphCoverage:
    """Verify graph integrity plus coverage of the requested reachable commits.

    Every commit reachable from the selected roots must be represented in the
    installed graph. Extra graph entries are intentionally allowed: an
    acceleration file can safely retain commits that have since become
    unreachable, and requiring exact equality would turn benign ref deletion
    into a verification failure.

    Repository-wide mode (``revisions is None``) uses the exact same all-refs
    plus HEAD selection as a normal write. Explicit roots use the same shallow-
    aware traversal as ``write --stdin-commits``.
    """
    graph = CommitGraph(repo.pygit_dir)
    graph.verify(repo.store)
    indexed = set(graph.entries)
    expected = {
        oid
        for oid, _tree, _parents in collect_commit_graph_commits(repo, revisions)
    }
    missing = sorted(expected - indexed)
    if missing:
        first = missing[0]
        raise CommitGraphError(
            f"commit-graph is missing {len(missing)} reachable commit(s); "
            f"first missing: {first}"
        )

    return CommitGraphCoverage(
        expected_count=len(expected),
        indexed_count=len(indexed),
        extra_count=len(indexed - expected),
    )
