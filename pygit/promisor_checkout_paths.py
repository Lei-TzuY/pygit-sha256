"""Pathspec-aware promisor batching for ``Repository.checkout_paths``.

Full commit transitions can batch every promised blob in the target snapshot, but
path-limited checkout must not defeat partial clone by faulting in unrelated
objects.  The historical ``checkout_paths`` implementation flattens the complete
target tree before applying pathspecs, which necessarily consumes every foreign
blob entry and can trigger one lazy fetch per unrelated promised object.

Phase219 replaces only the partial-clone path.  It walks the retained tree graph
without resolving unrelated blob entries, validates each literal pygit pathspec,
materializes exactly the selected unresolved blobs in one batch, and then writes
only those paths to the worktree and SHA-256-native index.  Ordinary repositories
continue to use the historical implementation unchanged.
"""

from __future__ import annotations

from functools import wraps
from typing import Dict, Iterable, List, Set, Tuple, Type

from .objects import CommitObject, TreeObject
from .promisor import promised_kind, read_promisor_state
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def _path_relevant(path: str, pathspec: str, *, is_dir: bool) -> bool:
    """Return whether ``path`` can contribute to the literal pygit pathspec."""
    if not pathspec:
        return True
    if is_dir:
        return (
            path == pathspec
            or path.startswith(f"{pathspec}/")
            or pathspec.startswith(f"{path}/")
        )
    return path == pathspec or path.startswith(f"{pathspec}/")


def _selected_blob_entries(repo, tree_sha: str, pathspec: str):
    """Yield selected ``(path, TreeEntry)`` pairs without resolving other blobs."""
    pending: List[Tuple[str, str]] = [(tree_sha, "")]
    while pending:
        current_tree_sha, prefix = pending.pop()
        tree = repo.store.read(current_tree_sha)
        if not isinstance(tree, TreeObject):
            raise RuntimeError("promisor checkout path traversal reached a non-tree object")
        for entry in tree.entries:
            path = entry.name if not prefix else f"{prefix}/{entry.name}"
            if not _path_relevant(path, pathspec, is_dir=entry.is_dir):
                continue
            if entry.is_dir:
                # Partial-clone filters retain trees, so directory identities are
                # locally resolvable and safe to traverse without blob network I/O.
                pending.append((entry.sha, path))
            else:
                yield path, entry


def collect_checkout_path_promises(repo, commit_sha: str, pathspecs: Iterable[str]) -> Set[str]:
    """Return unresolved promised blobs selected by the supplied literal pathspecs."""
    commit = repo.store.read(commit_sha)
    if not isinstance(commit, CommitObject):
        return set()

    promised: Set[str] = set()
    for pathspec in pathspecs:
        for _path, entry in _selected_blob_entries(repo, commit.tree, pathspec):
            if entry.is_resolved:
                continue
            if entry.native_oid and promised_kind(repo.pygit_dir, entry.native_oid):
                promised.add(entry.native_oid)
    return promised


def _selected_entries(repo, commit_sha: str, pathspecs: Iterable[str]) -> Dict[str, Tuple[str, str]]:
    """Resolve selected entries after any required promisor materialization."""
    commit = repo.store.read(commit_sha)
    if not isinstance(commit, CommitObject):
        raise ValueError(f"'{commit_sha}' does not point to a commit")
    selected: Dict[str, Tuple[str, str]] = {}
    for pathspec in pathspecs:
        for path, entry in _selected_blob_entries(repo, commit.tree, pathspec):
            selected[path] = (entry.sha, entry.mode)
    return selected


def install_promisor_checkout_paths_support(repository_cls: Type) -> None:
    """Install a path-aware partial-clone implementation of ``checkout_paths``."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_checkout_paths = repository_cls.checkout_paths

    @wraps(original_checkout_paths)
    def checkout_paths(self, paths: List[str], target: str = "HEAD") -> List[str]:
        state = read_promisor_state(self.pygit_dir)
        if not state.get("promised"):
            return original_checkout_paths(self, paths, target=target)

        target_sha = self._resolve_revision(target)
        commit = self._require_commit(target_sha)
        normalized = [self._normalize_pathspec(pathspec) for pathspec in paths]

        # Validate every pathspec before network or worktree/index side effects.
        matches_by_spec: List[List[str]] = []
        for original, pathspec in zip(paths, normalized):
            matches = [path for path, _entry in _selected_blob_entries(self, commit.tree, pathspec)]
            if not matches:
                raise KeyError(f"pathspec '{original}' did not match any files in {target}")
            matches_by_spec.append(matches)

        promises = collect_checkout_path_promises(self, target_sha, normalized)
        if promises:
            materialize_promised_objects(self.pygit_dir, sorted(promises))

        selected = _selected_entries(self, target_sha, normalized)
        restored: List[str] = []
        for matching in matches_by_spec:
            for path in matching:
                blob_sha, mode = selected[path]
                self._write_worktree_blob(path, blob_sha, mode)
                self.index.entries[path] = self._index_entry(path, blob_sha, mode)
                restored.append(path)

        self.index.save()
        return sorted(set(restored))

    repository_cls.checkout_paths = checkout_paths
    _INSTALLED = True
