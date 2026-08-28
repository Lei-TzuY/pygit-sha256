"""Pathspec-aware promisor batching for ``Repository.reset_paths``.

Partial clones may retain foreign tree entries whose blobs are intentionally
missing.  The historical ``reset_paths`` implementation flattens the entire
target commit before applying pathspecs, which resolves every blob entry and can
therefore fault in unrelated promised objects.

This module replaces only the partial-clone path.  It traverses the retained tree
graph according to pygit's existing literal file/directory pathspec semantics,
validates all pathspecs before network or index mutation, materializes only the
selected unresolved blobs, and then updates the SHA-256-native index.  Ordinary
repositories continue to use the historical implementation unchanged.
"""

from __future__ import annotations

from functools import wraps
from typing import Dict, Iterable, List, Set, Tuple, Type

from .objects import CommitObject
from .promisor import promised_kind, read_promisor_state
from .promisor_checkout_paths import _selected_blob_entries
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def _selected_target_entries(repo, commit_sha: str, pathspecs: Iterable[str]):
    """Yield selected target ``(path, TreeEntry)`` pairs without unrelated blobs."""
    commit = repo.store.read(commit_sha)
    if not isinstance(commit, CommitObject):
        raise ValueError(f"'{commit_sha}' does not point to a commit")
    for pathspec in pathspecs:
        yield from _selected_blob_entries(repo, commit.tree, pathspec)


def collect_reset_path_promises(repo, commit_sha: str, pathspecs: Iterable[str]) -> Set[str]:
    """Return unresolved promised blobs selected by reset pathspecs."""
    promised: Set[str] = set()
    for _path, entry in _selected_target_entries(repo, commit_sha, pathspecs):
        if entry.is_resolved:
            continue
        if entry.native_oid and promised_kind(repo.pygit_dir, entry.native_oid):
            promised.add(entry.native_oid)
    return promised


def _resolved_target_map(repo, commit_sha: str, pathspecs: Iterable[str]) -> Dict[str, Tuple[str, str]]:
    """Resolve selected target entries after required materialization."""
    selected: Dict[str, Tuple[str, str]] = {}
    for path, entry in _selected_target_entries(repo, commit_sha, pathspecs):
        selected[path] = (entry.sha, entry.mode)
    return selected


def install_promisor_reset_paths_support(repository_cls: Type) -> None:
    """Install a path-aware partial-clone implementation of ``reset_paths``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_reset_paths = repository_cls.reset_paths

    @wraps(original_reset_paths)
    def reset_paths(self, paths: List[str], target: str = "HEAD") -> Dict[str, object]:
        state = read_promisor_state(self.pygit_dir)
        if not state.get("promised"):
            return original_reset_paths(self, paths, target=target)

        operation = self._operation_name()
        if operation:
            raise RuntimeError(f"Cannot reset paths during a {operation} operation.")
        if not paths:
            raise RuntimeError("reset paths requires at least one pathspec.")

        target_sha = self._resolve_revision(target)
        commit = self._require_commit(target_sha)
        normalized = [self._normalize_pathspec(pathspec) for pathspec in paths]
        index_paths = set(self.index.paths())

        # Determine matches without resolving unrelated target blobs.  A pathspec
        # can validly select an index-only path, which reset removes when the
        # target commit does not contain it.
        target_matches_by_spec: List[List[str]] = []
        index_matches_by_spec: List[List[str]] = []
        for original, pathspec in zip(paths, normalized):
            target_matches = [
                path for path, _entry in _selected_blob_entries(self, commit.tree, pathspec)
            ]
            index_matches = sorted(
                path
                for path in index_paths
                if path == pathspec or path.startswith(f"{pathspec}/")
            )
            if not target_matches and not index_matches:
                raise KeyError(f"pathspec '{original}' did not match any files")
            target_matches_by_spec.append(target_matches)
            index_matches_by_spec.append(index_matches)

        # No network or index side effects occur until every pathspec is known to
        # be valid.  Materialization derives real content-based local SHA-256 ids
        # for only the target entries that the index will store.
        promises = collect_reset_path_promises(self, target_sha, normalized)
        if promises:
            materialize_promised_objects(self.pygit_dir, sorted(promises))

        target_map = _resolved_target_map(self, target_sha, normalized)
        changed: List[str] = []
        for target_matches, index_matches in zip(target_matches_by_spec, index_matches_by_spec):
            candidates = sorted(set(target_matches) | set(index_matches))
            for path in candidates:
                target_entry = target_map.get(path)
                if target_entry is None:
                    if path in self.index:
                        self.index.entries.pop(path, None)
                        changed.append(path)
                    continue
                blob_sha, mode = target_entry
                self.index.entries[path] = self._index_entry_for_blob(path, blob_sha, mode)
                changed.append(path)

        self.index.save()
        return {"status": "reset", "sha": target_sha, "paths": sorted(set(changed))}

    repository_cls.reset_paths = reset_paths
    _INSTALLED = True
