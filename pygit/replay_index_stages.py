"""Bridge cherry-pick and rebase conflicts into the persistent multi-stage index.

Replay conflicts use the same Git-style stage model as merges:

* stage 1: the picked commit's first parent (base)
* stage 2: the current HEAD receiving the replay (ours)
* stage 3: the picked commit itself (theirs)

The bridge patches the existing Repository class in place so legacy porcelain
code can keep sharing `_apply_three_way()` while modern plumbing sees real
unmerged index records.
"""

from __future__ import annotations

from typing import Iterable, Type

from .merge_index_stages import _conflict_stage_entry


def populate_replay_conflict_stages(repo, source_sha: str, conflicts: Iterable[str]) -> None:
    """Replace stage-0 records for replay conflicts with stages 1/2/3."""
    source = repo._require_commit(source_sha)
    if len(source.parents) > 1:
        raise RuntimeError("Cannot populate replay stages for a merge commit.")

    ours = repo.refs.resolve_head()
    if not ours:
        raise RuntimeError("Cannot populate replay conflict stages without HEAD.")

    base_tree = repo._commit_tree_entries(source.parents[0]) if source.parents else {}
    our_tree = repo._commit_tree_entries(ours)
    their_tree = repo._tree_entries(source.tree)

    for path in sorted(set(conflicts)):
        repo.index.entries.pop(path, None)
        repo.index.clear_unmerged(path)

        for stage, tree in ((1, base_tree), (2, our_tree), (3, their_tree)):
            value = tree.get(path)
            if value is None:
                continue
            repo.index.set_entry(_conflict_stage_entry(repo, path, value, stage))

    repo.index.save()


def _clear_unmerged(repo) -> None:
    if repo.index.has_unmerged():
        repo.index.clear_unmerged()
        repo.index.save()


def install_repository_replay_stage_support(repository_cls: Type[object]) -> None:
    """Install Phase 129 cherry-pick/rebase stage integration once."""
    if getattr(repository_cls, "_phase129_replay_stage_support", False):
        return

    original_apply_cherry_pick = repository_cls._apply_cherry_pick
    original_clear_cherry_pick_state = repository_cls._clear_cherry_pick_state
    original_clear_rebase_state = repository_cls._clear_rebase_state
    original_rebase_skip = repository_cls.rebase_skip

    def _apply_cherry_pick(self, source_sha: str, target: str):
        conflicts = original_apply_cherry_pick(self, source_sha, target)
        if conflicts:
            populate_replay_conflict_stages(self, source_sha, conflicts)
        return conflicts

    def _clear_cherry_pick_state(self) -> None:
        had_state = self._read_cherry_pick_state() is not None
        original_clear_cherry_pick_state(self)
        if had_state:
            _clear_unmerged(self)

    def _clear_rebase_state(self) -> None:
        had_state = self._read_rebase_state() is not None
        original_clear_rebase_state(self)
        if had_state:
            _clear_unmerged(self)

    def rebase_skip(self, committer_name: str = "Unknown", committer_email: str = "unknown@example.com"):
        state = self._read_rebase_state()
        if state and state.get("current"):
            # The original implementation restores HEAD and immediately starts
            # replaying the next commit. Remove the skipped commit's unmerged
            # records before that continuation so write-tree/commit guards do
            # not observe stale stages.
            _clear_unmerged(self)
        return original_rebase_skip(self, committer_name, committer_email)

    repository_cls._apply_cherry_pick = _apply_cherry_pick
    repository_cls._clear_cherry_pick_state = _clear_cherry_pick_state
    repository_cls._clear_rebase_state = _clear_rebase_state
    repository_cls.rebase_skip = rebase_skip
    repository_cls._phase129_replay_stage_support = True
