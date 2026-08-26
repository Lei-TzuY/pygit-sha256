"""
pygit/commit_graph.py
=====================
Commit-graph acceleration file writer, strict parser, and verifier.

The repository keeps an intentionally small educational binary format at
``.pygit/objects/info/commit-graph``::

    CGPH | version:u8 | chunk-count:u8 | commit-count:u32
    repeated:
        commit-oid:32 | tree-oid:32 | generation:u32 | parent-count:u16
        parent-oid:32 * parent-count

This is pygit's own SHA-256 format, not Git's native commit-graph format.
"""

from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .objects import CommitObject, TreeObject


GraphEntry = Tuple[str, List[str], int]


class CommitGraphError(ValueError):
    """Raised when a commit-graph file or graph input violates the format."""


@dataclass(frozen=True)
class CommitGraphVerification:
    """Summary returned after strict commit-graph verification."""

    path: Path
    commit_count: int
    max_generation: int


def _validate_oid(oid: str, label: str) -> None:
    if len(oid) != 64:
        raise CommitGraphError(f"{label} must be a 64-character SHA-256 object id")
    try:
        int(oid, 16)
    except ValueError as exc:
        raise CommitGraphError(f"{label} is not hexadecimal: {oid!r}") from exc
    if oid != oid.lower():
        raise CommitGraphError(f"{label} must use canonical lowercase hexadecimal")


class CommitGraph:
    """Manage pygit's commit-graph acceleration file."""

    MAGIC = b"CGPH"
    VERSION = 1
    CHUNK_COUNT = 1
    HEADER_SIZE = 10
    ENTRY_FIXED_SIZE = 70

    def __init__(self, pygit_dir: Path) -> None:
        self.graph_file = pygit_dir / "objects" / "info" / "commit-graph"
        self.entries: Dict[str, GraphEntry] = {}

    @staticmethod
    def _compute_generations(
        commit_map: Dict[str, Tuple[str, List[str]]]
    ) -> Dict[str, int]:
        generations: Dict[str, int] = {}
        visiting: set[str] = set()

        def get_gen(sha: str) -> int:
            if sha in generations:
                return generations[sha]
            if sha not in commit_map:
                # A parent can legitimately sit outside the graph, for example at
                # a shallow boundary. Preserve the historical pygit convention
                # that such an external parent has generation 1.
                return 1
            if sha in visiting:
                raise CommitGraphError(f"commit-graph cycle detected at {sha}")

            visiting.add(sha)
            _, parents = commit_map[sha]
            gen = 1 if not parents else 1 + max(get_gen(parent) for parent in parents)
            visiting.remove(sha)
            generations[sha] = gen
            return gen

        for sha in commit_map:
            get_gen(sha)
        return generations

    @classmethod
    def _serialize(cls, commits: List[Tuple[str, str, List[str]]]) -> bytes:
        commit_map: Dict[str, Tuple[str, List[str]]] = {}
        for sha, tree_sha, parents in commits:
            _validate_oid(sha, "commit id")
            _validate_oid(tree_sha, f"tree id for {sha}")
            for parent in parents:
                _validate_oid(parent, f"parent id for {sha}")
            if sha in commit_map:
                raise CommitGraphError(f"duplicate commit id in commit-graph input: {sha}")
            commit_map[sha] = (tree_sha, list(parents))

        generations = cls._compute_generations(commit_map)
        body = bytearray(cls.MAGIC)
        body.extend(struct.pack(">BBI", cls.VERSION, cls.CHUNK_COUNT, len(commit_map)))

        for sha in sorted(commit_map):
            tree_sha, parents = commit_map[sha]
            if len(parents) > 0xFFFF:
                raise CommitGraphError(f"too many parents for commit {sha}")
            body.extend(bytes.fromhex(sha))
            body.extend(bytes.fromhex(tree_sha))
            body.extend(struct.pack(">IH", generations[sha], len(parents)))
            for parent in parents:
                body.extend(bytes.fromhex(parent))
        return bytes(body)

    @classmethod
    def _parse_bytes(cls, data: bytes) -> Dict[str, GraphEntry]:
        if len(data) < cls.HEADER_SIZE:
            raise CommitGraphError("commit-graph is truncated before its header")
        if data[:4] != cls.MAGIC:
            raise CommitGraphError("invalid commit-graph signature")

        version, chunk_count, num_commits = struct.unpack(">BBI", data[4:10])
        if version != cls.VERSION:
            raise CommitGraphError(
                f"unsupported commit-graph version {version}; expected {cls.VERSION}"
            )
        if chunk_count != cls.CHUNK_COUNT:
            raise CommitGraphError(
                f"unsupported commit-graph chunk count {chunk_count}; "
                f"expected {cls.CHUNK_COUNT}"
            )

        minimum_size = cls.HEADER_SIZE + num_commits * cls.ENTRY_FIXED_SIZE
        if minimum_size > len(data):
            raise CommitGraphError(
                f"commit-graph declares {num_commits} commits but is truncated"
            )

        offset = cls.HEADER_SIZE
        result: Dict[str, GraphEntry] = {}
        previous_sha: Optional[str] = None

        for index in range(num_commits):
            if offset + cls.ENTRY_FIXED_SIZE > len(data):
                raise CommitGraphError(f"commit-graph entry {index} is truncated")

            sha = data[offset : offset + 32].hex()
            tree_sha = data[offset + 32 : offset + 64].hex()
            generation, parent_count = struct.unpack(
                ">IH", data[offset + 64 : offset + cls.ENTRY_FIXED_SIZE]
            )
            offset += cls.ENTRY_FIXED_SIZE

            if previous_sha is not None and sha <= previous_sha:
                raise CommitGraphError(
                    "commit-graph object ids must be strictly sorted and unique"
                )
            previous_sha = sha
            if generation < 1:
                raise CommitGraphError(f"invalid generation 0 for commit {sha}")

            parent_bytes = parent_count * 32
            if offset + parent_bytes > len(data):
                raise CommitGraphError(f"parent list for commit {sha} is truncated")
            parents = [
                data[pos : pos + 32].hex()
                for pos in range(offset, offset + parent_bytes, 32)
            ]
            offset += parent_bytes

            if sha in parents:
                raise CommitGraphError(f"commit {sha} names itself as a parent")
            result[sha] = (tree_sha, parents, generation)

        if offset != len(data):
            raise CommitGraphError(
                f"commit-graph has {len(data) - offset} trailing byte(s)"
            )

        generation_inputs = {
            sha: (tree_sha, parents)
            for sha, (tree_sha, parents, _generation) in result.items()
        }
        expected = cls._compute_generations(generation_inputs)
        for sha, (_tree_sha, _parents, generation) in result.items():
            if generation != expected[sha]:
                raise CommitGraphError(
                    f"generation mismatch for commit {sha}: "
                    f"stored {generation}, expected {expected[sha]}"
                )

        return result

    def write(self, commits: List[Tuple[str, str, List[str]]]) -> Path:
        """Atomically write a fully validated commit-graph file."""
        data = self._serialize(commits)
        # Parse our own output before installation so malformed graph bytes can
        # never replace the current acceleration file.
        self._parse_bytes(data)

        self.graph_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".commit-graph.",
            dir=str(self.graph_file.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.graph_file)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return self.graph_file

    def read(self) -> Dict[str, GraphEntry]:
        """Read and strictly parse the commit-graph file if present."""
        if not self.graph_file.exists():
            self.entries = {}
            return {}

        result = self._parse_bytes(self.graph_file.read_bytes())
        self.entries = result
        return result

    def verify(self, store=None) -> CommitGraphVerification:
        """Strictly verify graph structure and, optionally, stored commit metadata."""
        if not self.graph_file.exists():
            raise FileNotFoundError(f"commit-graph not found: {self.graph_file}")

        entries = self.read()
        if store is not None:
            for sha, (tree_sha, parents, _generation) in entries.items():
                try:
                    obj = store.read(sha)
                except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
                    raise CommitGraphError(
                        f"commit-graph references missing commit object {sha}"
                    ) from exc
                if not isinstance(obj, CommitObject):
                    raise CommitGraphError(f"commit-graph object {sha} is not a commit")
                if obj.tree != tree_sha:
                    raise CommitGraphError(
                        f"tree mismatch for commit {sha}: "
                        f"graph {tree_sha}, object {obj.tree}"
                    )
                if list(obj.parents) != parents:
                    raise CommitGraphError(f"parent mismatch for commit {sha}")
                try:
                    tree = store.read(tree_sha)
                except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
                    raise CommitGraphError(
                        f"commit {sha} references missing tree {tree_sha}"
                    ) from exc
                if not isinstance(tree, TreeObject):
                    raise CommitGraphError(
                        f"tree id {tree_sha} for commit {sha} is not a tree"
                    )

        return CommitGraphVerification(
            path=self.graph_file,
            commit_count=len(entries),
            max_generation=max(
                (generation for _tree, _parents, generation in entries.values()),
                default=0,
            ),
        )
