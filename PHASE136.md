# Phase 136 — rev-list parent-count filters

Phase 136 adds Git-style commit limiting by parent count. Unlike presentation-only flags, these options change the emitted commit set while leaving ancestry traversal intact.

## Commands

```bash
pygit rev-list --merges HEAD
pygit rev-list --no-merges HEAD
pygit rev-list --min-parents=3 HEAD
pygit rev-list --max-parents=0 --all
pygit rev-list --merges --boundary release..HEAD
pygit rev-list --objects --merges HEAD
```

`--merges` is exactly `--min-parents=2`; `--no-merges` is exactly `--max-parents=1`. `--max-parents=0` selects roots, while `--min-parents=3` selects octopus merges. A negative maximum means no upper limit, so `--max-parents=-1` and `--no-max-parents` reset the upper bound. `--no-min-parents` resets the lower bound to zero.

When multiple aliases/reset forms are supplied, normal command-line order applies: the later option for the same bound wins.

## Selection order

Parent-count filtering happens after revision traversal/range exclusion but before `--skip` and `--max-count`. `--reverse` remains the final presentation transform. This matters for commands such as:

```bash
pygit rev-list --no-merges --skip 1 -n 1 HEAD
```

The command skips the first non-merge commit, rather than limiting the unfiltered walk first and filtering afterward.

`--first-parent` restricts ancestry traversal, but parent-count classification still uses the commit's traversal-visible parent list. A real merge therefore remains a merge under `--first-parent`. Commits recorded in `.pygit/shallow` remain synthetic roots and count as zero-parent commits.

## Composition

The filter is shared by the CLI's commit-selection paths rather than applied as a final text filter:

- `--count` counts filtered commits;
- `--parents` keeps each emitted commit's real parent metadata;
- `--children` preserves Phase 134's pre-limit child mapping, so a displayed merge may still name a direct non-merge child that was filtered from the record stream;
- `--boundary` treats direct parents outside the filtered visible set as excluded boundary commits;
- `--left-right`, `--left-only`, and `--right-only` retain side semantics before output limits;
- `--objects` expands only the object closure required by filtered commits, so objects reachable solely from filtered-out commits are not pulled back in;
- `--objects-edge` keeps Phase 121's revision-edge protocol while the object payload follows the filtered commit set.

## Python API

```python
from pygit.rev_list_parent_filter import (
    rev_list_parent_filter,
    rev_list_parent_filter_boundary,
    rev_list_parent_filter_children,
    rev_list_parent_filter_named_objects,
)

merges = rev_list_parent_filter(repo, ["HEAD"], min_parents=2)
roots = rev_list_parent_filter(repo, all_refs=True, max_parents=0)
```

`normalize_parent_limits()` validates the lower bound and normalizes any negative upper bound to the unlimited form.

## Compatibility

The semantics follow current Git documentation: `--merges` equals `--min-parents=2`, `--no-merges` equals `--max-parents=1`, zero maximum selects roots, and negative maximum values remove the upper bound. The implementation remains read-only and does not modify refs, index state, objects, packs, reflogs, or the worktree.
