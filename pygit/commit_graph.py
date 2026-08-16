"""
pygit/commit_graph.py
=====================
Commit Graph Acceleration File Generator & Parser

Generates and reads binary commit-graph files stored at ``.pygit/objects/info/commit-graph``.
Format: Magic header (CGPH), fanout table, commit data entries (SHA, Tree SHA, Parent SHAs, Generation).
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class CommitGraph:
    """Manages the commit-graph acceleration file."""

    MAGIC = b"CGPH"
    VERSION = 1

    def __init__(self, pygit_dir: Path) -> None:
        self.graph_file = pygit_dir / "objects" / "info" / "commit-graph"
        self.entries: Dict[str, Tuple[str, List[str], int]] = {}  # sha -> (tree_sha, parents, generation)

    def write(self, commits: List[Tuple[str, str, List[str]]]) -> Path:
        """
        Write commit DAG data to commit-graph file.
        commits: List of (sha, tree_sha, parents)
        """
        self.graph_file.parent.mkdir(parents=True, exist_ok=True)

        # Compute generation numbers via topological sorting
        generations: Dict[str, int] = {}
        commit_map = {sha: (tree, parents) for sha, tree, parents in commits}

        def get_gen(sha: str, visited: set) -> int:
            if sha in generations:
                return generations[sha]
            if sha in visited or sha not in commit_map:
                return 1
            visited.add(sha)
            _, parents = commit_map[sha]
            if not parents:
                gen = 1
            else:
                gen = 1 + max(get_gen(p, visited) for p in parents)
            generations[sha] = gen
            return gen

        for sha, _, _ in commits:
            get_gen(sha, set())

        # Sort commits lexicographically by SHA
        sorted_commits = sorted(commits, key=lambda c: c[0])

        # Write binary format
        body = bytearray()
        body.extend(self.MAGIC)
        body.extend(struct.pack(">BB", self.VERSION, 1))  # 1 chunk count
        body.extend(struct.pack(">I", len(sorted_commits)))

        for sha, tree_sha, parents in sorted_commits:
            gen = generations.get(sha, 1)
            # Store 32-byte sha, 32-byte tree_sha, gen (4 bytes), parent_count (2 bytes)
            body.extend(bytes.fromhex(sha))
            body.extend(bytes.fromhex(tree_sha))
            body.extend(struct.pack(">IH", gen, len(parents)))
            for p in parents:
                body.extend(bytes.fromhex(p))

        self.graph_file.write_bytes(bytes(body))
        return self.graph_file

    def read(self) -> Dict[str, Tuple[str, List[str], int]]:
        """Read and parse the commit-graph binary file if present."""
        if not self.graph_file.exists():
            return {}

        data = self.graph_file.read_bytes()
        if len(data) < 10 or data[:4] != self.MAGIC:
            return {}

        version, chunk_cnt = struct.unpack(">BB", data[4:6])
        num_commits = struct.unpack(">I", data[6:10])[0]

        offset = 10
        result: Dict[str, Tuple[str, List[str], int]] = {}

        for _ in range(num_commits):
            if offset + 70 > len(data):
                break
            sha = data[offset:offset + 32].hex()
            tree_sha = data[offset + 32:offset + 64].hex()
            gen, p_count = struct.unpack(">IH", data[offset + 64:offset + 70])
            offset += 70

            parents: List[str] = []
            for _ in range(p_count):
                if offset + 32 > len(data):
                    break
                parents.append(data[offset:offset + 32].hex())
                offset += 32

            result[sha] = (tree_sha, parents, gen)

        self.entries = result
        return result
