# Phase 137 — rev-list oldest-N limiting

Phase 137 adds Git 2.55-style `rev-list --max-count-oldest=<N>`, which selects the oldest N commits that would otherwise be shown instead of the usual newest N selected by `--max-count`.

## Commands

```bash
pygit rev-list --max-count-oldest=3 HEAD
pygit rev-list --reverse --max-count-oldest=3 HEAD
pygit rev-list --no-merges --max-count-oldest=5 HEAD
pygit rev-list --left-right --max-count-oldest=2 A...B
pygit rev-list --children --max-count-oldest=2 HEAD
pygit rev-list --boundary --max-count-oldest=1 release..HEAD
pygit rev-list --objects --max-count-oldest=2 HEAD
```

## Selection order

The option is a commit-limiting operation, not a presentation shortcut. Revision/range selection, side filtering, and parent-count filtering happen first. The oldest N records are then taken from the normal commit order. `--reverse` is applied only after that slice, so reversing an oldest-three walk displays those same three commits oldest-first.

A zero limit selects no commits. A limit larger than the selected history is a no-op.

Current upstream Git explicitly rejects combining `--max-count-oldest` with either `--max-count` or `--skip`; pygit does the same rather than inventing an ambiguous ordering between those limits. Negative oldest counts are rejected.

## Metadata and boundaries

`--children` retains Phase 134's pre-limit child metadata. This means an oldest commit may name a newer child that was omitted by the oldest-N slice, just as `--max-count` can preserve a child hidden by its limit.

`--boundary` computes boundaries from the final oldest-N visible commit set. Excluded parents adjacent to those commits are emitted with the usual `-` prefix. `--parents`, `--first-parent`, shallow roots, side markers, and `--count` continue to use their existing semantics.

## Object mode

`--objects --max-count-oldest=N` expands only the selected oldest commits' tree/blob closure. Commits omitted by the oldest-N limit are not pulled back into the object stream through parent traversal. Existing negative-range object-closure subtraction is preserved.

`--objects-edge` continues to advertise the complete revision-range object edge independently of the visible commit limit, consistent with Phase 121.

## Python API

```python
from pygit.rev_list_oldest import (
    rev_list_oldest,
    rev_list_oldest_boundary,
    rev_list_oldest_children,
    rev_list_oldest_named_objects,
)

entries = rev_list_oldest(repo, ["HEAD"], max_count_oldest=3)
```

The helpers are read-only and leave refs, reflogs, indexes, worktrees, and object storage untouched.

## Compatibility boundary

This phase intentionally follows the Git 2.55 oldest-count behavior and its incompatibility with `--max-count` / `--skip`. Date limiting, message/identity grep filters, path-limited history, reflog walks, and pretty formatting remain separate work.
