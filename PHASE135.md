# Phase 135 — rev-list excluded boundary commits

Phase 135 adds native-style `rev-list --boundary` presentation on top of the existing Phase 68 revision selector and the Phase 133/134 parent/child metadata layers.

## Commands

```bash
pygit rev-list --boundary release..HEAD
pygit rev-list --boundary -n 1 HEAD
pygit rev-list --reverse --boundary release..HEAD
pygit rev-list --left-right --boundary A...B
pygit rev-list --parents --boundary release..HEAD
pygit rev-list --children --boundary release..HEAD
pygit rev-list --objects --boundary release..HEAD
```

Boundary commits are excluded commits immediately adjacent to the visible commit set and are prefixed with `-`.

## Limit semantics

`--boundary` is deliberately different from Phase 121 `--objects-edge`.

`--objects-edge` advertises the complete revision-range object edge before `--skip` / `--max-count`. `--boundary`, like native Git, observes the visible limited commit set. Therefore `pygit rev-list --boundary -n 1 HEAD` emits the selected tip and its traversal parent(s) as boundary commits, even though those parents would normally have been selected without the limit.

`--reverse` is a final presentation transform over the combined selected-plus-boundary stream, so boundary records move to the front when the entire stream is reversed.

Boundary candidates use the same date or topological ordering policy as selected commits rather than raw stored parent order. `--first-parent` restricts which parent edge may become a boundary. A shallow commit remains a synthetic root and does not advertise its hidden stored parent.

## Metadata composition

`--parents` prints the real traversal-visible parents for boundary commits as well as selected commits. `--children` gives a boundary commit the visible child or children that reach it. Selected child metadata keeps Phase 134's pre-limit semantics.

With `--left-right`, selected commits retain `<` / `>` markers while boundary commits use `-`, matching Git's output protocol. Counting includes boundary records; the side-aware count keeps Git's existing left/right accounting behaviour for boundary commits.

## Object mode

`--objects --boundary` emits selected commit records, then boundary commit records, then the selected named tree/blob closure. Boundary commits are not expanded into object closure because they are excluded commits. `--objects --boundary --count` includes the boundary records in the count.

`--objects-edge --boundary` keeps Phase 121's pre-limit edge advertisement and additionally reports any visible-set boundary created by limiting. If both mechanisms identify the same commit, it is printed only once.

## Python API

```python
from pygit.rev_list_boundary import (
    RevListBoundaryEntry,
    boundary_children,
    rev_list_boundary,
)

entries = rev_list_boundary(repo, ["release..HEAD"], topo_order=True)
```

`RevListBoundaryEntry.boundary` distinguishes excluded boundary records from ordinary selected records. The operation is read-only.

## Compatibility boundary

This phase implements commit/object boundary presentation and composes it with the revision-set, ordering, side, parent, child, count, shallow, and object-name behaviour already present in pygit. Path-limited history, reflog walks, missing/promisor-object modes, pretty formatting, and the newer NUL metadata protocol remain separate future work.
