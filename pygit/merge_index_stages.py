"""Bridge high-level merge conflicts into pygit's persistent multi-stage index.

Phase 124 introduced Git-style index stages 1 (base), 2 (ours), and 3
(theirs), while the older porcelain merge path still tracked conflicts only in
``MERGE_CONFLICTS`` and left a stage-0 entry behind.  This module connects the
two models without changing the three-way merge algorithm itself.

The installer intentionally patches the existing :class:`Repository` class in
place.  Python imports a package's ``__init__`` before its submodules, so the
same class object is seen by ``from pygit import Repository`` and by internal
``from .repo import Repository`` imports.  Keeping the bridge isolated also
avoids growing the already-large ``repo.py`` porcelain module.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple, Type

from .index import IndexEntry
from .objects import BlobObject


TreeEntry = Tuple[str, str]


def _conflict_stage_entry(repo, path: str, value: TreeEntry, stage: int) -> IndexEntry:
    """Create one stat-neutral unmerged index record for an existing object."""
    oid, mode = value
    obj = repo.store.read(oid)
    size = len(obj.serialize()) if isinstance(obj, BlobObject) else 0
    return IndexEntry(
        path=path,
        sha=oid,
        mode=mode,
        size=size,
        mtime=0.0,
        stage=stage,
    )


def populate_merge_conflict_stages(
    repo,
    merge_head: str,
    conflicts: Iterable[str],
    original_head: Optional[str] = None,
) -> None:
    """Replace conflict stage-0 records with Git-style stages 1/2/3.

    Missing sides are intentionally omitted.  For example, modify/delete
    conflicts can have stages 1 and 2 but no stage 3, while add/add conflicts
    have stages 2 and 3 but no stage 1.
    """
    ours = original_head or repo.refs.resolve_head()
    if not ours:
        raise RuntimeError("Cannot populate merge conflict stages without our HEAD.")

    base = repo._find_merge_base(ours, merge_head)
    base_tree = repo._commit_tree_entries(base) if base else {}
    our_tree = repo._commit_tree_entries(ours)
    their_tree = repo._commit_tree_entries(merge_head)

    for path in sorted(set(conflicts)):
        repo.index.entries.pop(path, None)
        repo.index.clear_unmerged(path)

        for stage, tree in ((1, base_tree), (2, our_tree), (3, their_tree)):
            value = tree.get(path)
            if value is None:
                continue
            repo.index.set_entry(_conflict_stage_entry(repo, path, value, stage))

    repo.index.save()


def install_repository_merge_stage_support(repository_cls: Type[object]) -> None:
    """Install Phase 127 merge-stage integration once for *repository_cls*."""
    if getattr(repository_cls, "_phase127_merge_stage_support", False):
        return

    original_write_merge_state = repository_cls._write_merge_state
    original_clear_merge_state = repository_cls._clear_merge_state

    def _write_merge_state(
        self,
        merge_head: str,
        conflicts: list[str],
        original_head: Optional[str] = None,
    ) -> None:
        # Publish the stage model before the operation metadata.  If stage
        # construction fails, callers do not observe a half-created MERGE_HEAD.
        populate_merge_conflict_stages(self, merge_head, conflicts, original_head)
        original_write_merge_state(self, merge_head, conflicts, original_head)

    def _clear_merge_state(self) -> None:
        # ``commit()`` calls _clear_merge_state even for ordinary commits, so
        # only clear unmerged entries when an actual MERGE_HEAD existed.
        had_merge = self._read_merge_head() is not None
        original_clear_merge_state(self)
        if had_merge and self.index.has_unmerged():
            self.index.clear_unmerged()
            self.index.save()

    repository_cls._write_merge_state = _write_merge_state
    repository_cls._clear_merge_state = _clear_merge_state
    repository_cls._phase127_merge_stage_support = True
