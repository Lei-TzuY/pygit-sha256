"""Promisor-aware batching for history content readers.

Phase226 closes partial-clone demand-fetch waterfalls in history commands that
consume complete commit snapshots outside ``Repository.diff``.  ``show`` reads
a commit and its first parent directly through ``_render_diff``; ``log -L`` and
``log --follow`` flatten snapshots while walking history.  Unresolved foreign
tree entries otherwise fault in one blob at a time.

The wrappers below only predict object demand.  Existing Repository methods stay
responsible for revision errors, log filtering/order, diff rendering, line-range
semantics, rename-follow behavior, and returned values.
"""

from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Iterable, Optional, Set, Type

from .objects import CommitObject
from .promisor import read_promisor_state
from .promisor_checkout import collect_checkout_promises
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False


def collect_history_promises(repo, commit_shas: Iterable[str]) -> Set[str]:
    """Return one deduplicated promised-blob set for commit snapshots."""
    promises: Set[str] = set()
    seen: Set[str] = set()
    for sha in commit_shas:
        if not sha or sha in seen:
            continue
        seen.add(sha)
        promises.update(collect_checkout_promises(repo, sha))
    return promises


def prefetch_history_promises(repo, commit_shas: Iterable[str]) -> Set[str]:
    """Materialize the predictable history-content demand in one request."""
    promises = collect_history_promises(repo, commit_shas)
    if promises:
        materialize_promised_objects(repo.pygit_dir, sorted(promises))
    return promises


def _parse_time(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None


def _passes_content_filters(
    commit: CommitObject,
    *,
    author: Optional[str],
    grep: Optional[str],
    since_ts: Optional[float],
    until_ts: Optional[float],
    merges_only: Optional[bool],
    min_parents: Optional[int],
    max_parents: Optional[int],
) -> bool:
    """Mirror the metadata-only filters that run before log content access."""
    if since_ts and commit.author.timestamp < since_ts:
        return False
    if until_ts and commit.author.timestamp > until_ts:
        return False
    if author:
        identity = f"{commit.author.name} <{commit.author.email}>"
        if author.lower() not in identity.lower():
            return False
    if grep and grep.lower() not in commit.message.lower():
        return False
    parent_count = len(commit.parents)
    if min_parents is not None and parent_count < min_parents:
        return False
    if max_parents is not None and parent_count > max_parents:
        return False
    if merges_only is True and parent_count < 2:
        return False
    if merges_only is False and parent_count >= 2:
        return False
    return True


def plan_log_content_snapshots(
    repo,
    *,
    start: Optional[str] = None,
    all_branches: bool = False,
    author: Optional[str] = None,
    grep: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    first_parent: bool = False,
    merges_only: Optional[bool] = None,
    min_parents: Optional[int] = None,
    max_parents: Optional[int] = None,
) -> Set[str]:
    """Plan snapshots that ``log -L``/``--follow`` may flatten.

    ``max_count`` intentionally does not truncate this planner: the historical
    log implementation counts commits only *after* content filtering.  Knowing
    where that limit lands would itself require reading the promised content.
    """
    shallow_file = repo.pygit_dir / "shallow"
    shallow = set()
    if shallow_file.exists():
        shallow = {
            line.strip()
            for line in shallow_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    seeds = []
    if all_branches:
        for branch in repo.refs.list_branches():
            sha = repo.refs.get_branch(branch)
            if sha:
                seeds.append(sha)
        if not seeds:
            head = repo.refs.resolve_head()
            if head:
                seeds.append(head)
    else:
        sha = repo._resolve_revision(start) if start else repo.refs.resolve_head()
        if sha:
            seeds.append(sha)

    since_ts = _parse_time(since)
    until_ts = _parse_time(until)
    snapshots: Set[str] = set()
    seen: Set[str] = set()
    queue = list(seeds)

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)

        obj = repo.store.read(current)
        if not isinstance(obj, CommitObject):
            continue

        if _passes_content_filters(
            obj,
            author=author,
            grep=grep,
            since_ts=since_ts,
            until_ts=until_ts,
            merges_only=merges_only,
            min_parents=min_parents,
            max_parents=max_parents,
        ):
            snapshots.add(current)
            # Both -L and --follow can inspect the first parent while processing
            # the current commit.  Include it now even if later traversal filters
            # would skip that parent's own content check.
            if obj.parents:
                snapshots.add(obj.parents[0])

        if current not in shallow:
            if first_parent and obj.parents:
                queue.append(obj.parents[0])
            else:
                queue.extend(obj.parents)

    return snapshots


def _value(args, kwargs, name: str, index: int, default):
    if name in kwargs:
        return kwargs[name]
    if len(args) > index:
        return args[index]
    return default


def install_promisor_history_support(repository_cls: Type) -> None:
    """Batch promises before ``show`` and content-reading ``log`` modes."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_show = repository_cls.show
    original_log = repository_cls.log

    @wraps(original_show)
    def show(self, target: str = "HEAD", stat: bool = False) -> str:
        state = read_promisor_state(self.pygit_dir)
        if state.get("promised"):
            # Resolve exactly as the historical implementation does.  If the
            # target is invalid, let the same resolver error surface before any
            # network request.
            sha = self._resolve_revision(target)
            commit = self._require_commit(sha)
            snapshots = [sha]
            if commit.parents:
                snapshots.append(commit.parents[0])
            prefetch_history_promises(self, snapshots)
        return original_show(self, target=target, stat=stat)

    @wraps(original_log)
    def log(self, *args, **kwargs):
        state = read_promisor_state(self.pygit_dir)
        if state.get("promised"):
            follow = _value(args, kwargs, "follow", 8, None)
            line_range = _value(args, kwargs, "line_range", 11, None)
            if follow or line_range:
                snapshots = plan_log_content_snapshots(
                    self,
                    start=_value(args, kwargs, "start", 0, None),
                    all_branches=_value(args, kwargs, "all_branches", 2, False),
                    author=_value(args, kwargs, "author", 3, None),
                    grep=_value(args, kwargs, "grep", 4, None),
                    since=_value(args, kwargs, "since", 5, None),
                    until=_value(args, kwargs, "until", 6, None),
                    first_parent=_value(args, kwargs, "first_parent", 12, False),
                    merges_only=_value(args, kwargs, "merges_only", 10, None),
                    min_parents=_value(args, kwargs, "min_parents", 13, None),
                    max_parents=_value(args, kwargs, "max_parents", 14, None),
                )
                if snapshots:
                    prefetch_history_promises(self, sorted(snapshots))
        return original_log(self, *args, **kwargs)

    repository_cls.show = show
    repository_cls.log = log
    _INSTALLED = True
