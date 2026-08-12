"""
pygit/graph_viz.py
==================
Terminal ASCII DAG History Graph Layout Renderer
================================================

Renders multi-column ASCII DAG graphs with branch pointers and commit node markers.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


def render_dag_graph(
    commits: List[Tuple[str, object]],
    ref_map: Dict[str, List[str]],
) -> List[str]:
    """
    Render ASCII DAG graph lines for a list of (sha, commit_object) tuples.
    """
    lines: List[str] = []
    columns: List[str] = []

    for sha, commit in commits:
        short_sha = sha[:7]
        refs = ref_map.get(sha, [])
        ref_str = f" ({', '.join(refs)})" if refs else ""
        title = commit.message.splitlines()[0] if commit.message else ""

        # Node marker line
        line_str = f"* {short_sha}{ref_str} {title}"
        lines.append(line_str)

        # Draw parent connection lines if multiple parents (merge)
        if len(commit.parents) > 1:
            lines.append("| \\")
        elif commit.parents:
            lines.append("|")

    return lines
