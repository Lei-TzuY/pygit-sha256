# Phase 75: `rev-list --objects` object-closure plumbing

Phase 75 extends the Phase 68 commit-set engine with read-only object enumeration. The goal is not merely to print extra SHA-256 IDs: the emitted object set follows the same positive/negative revision semantics that drive pack negotiation, including subtraction of trees and blobs already reachable from an excluded side.

## CLI

```bash
pygit rev-list --objects HEAD
pygit rev-list --objects release..HEAD
pygit rev-list --objects left...right
pygit rev-list --objects --all
pygit rev-list --objects --first-parent HEAD
pygit rev-list --objects --topo-order --skip 2 -n 5 HEAD
```

Phase 75 originally exposed an OID-only CLI stream. Phase 121 later adds native-style pathname annotations by default, `--no-object-names`, object counting, and `--objects-edge`, while preserving Phase 75's exact selected object set and the original `rev_list_objects()` Python API.

`--objects` and `--objects-edge` remain rejected with `--left-right` because the named-object stream and Phase 68 side-marker protocol are still separate. `--objects --count`, which Phase 75 originally rejected, is implemented by Phase 121 and counts the selected objects.

## Selection semantics

For ordinary positive/negative revision sets, Phase 75 computes the selected commit set first, then expands only those selected commits through their trees. It separately computes the complete object closure of negative roots and subtracts that closure. Therefore:

```text
A..B  =>  objects needed by selected B-side commits minus objects already reachable from A
```

The subtraction applies to commits, trees, and blobs. A blob still referenced by a selected merge snapshot is omitted when the negative side already owns it.

For `A...B`, the commit selection remains the symmetric difference from Phase 68. The complete common-ancestry object closure is then subtracted, so shared trees/blobs from the merge-base history are not emitted again.

## Limiting and ordering

`--skip` and `--max-count` are applied to the commit set before object expansion. This matters for script-facing behavior: `rev-list --objects -n 1 HEAD` enumerates the selected tip commit and the objects required by that snapshot, but does not accidentally reintroduce omitted parent commits merely because object traversal is recursive.

`--first-parent` and `.pygit/shallow` boundaries affect commit selection and negative/common-ancestry subtraction consistently. `--topo-order` and `--reverse` continue to control selected commit order. The Phase 75 API keeps its deterministic OID-sorted non-commit tail; Phase 121's CLI presentation overlays tree-walk pathname ordering without changing the set.

## Shared reachability primitive

The existing `pygit.pack_objects.reachable_objects()` walker is reused instead of introducing another object graph implementation. Phase 75 extends it with optional controls:

- `follow_commit_parents=False`: expand selected commits through trees without walking their parents;
- `first_parent=True`: when commit ancestry is followed, traverse only the first parent.

The default arguments preserve the existing Phase 67 `pack-objects` behavior.

## Python API

```python
from pygit import RevListObjectEntry, rev_list_objects

entries = rev_list_objects(
    repo,
    ["release..HEAD"],
    topo_order=True,
    max_count=20,
)
for entry in entries:
    print(entry.type_name, entry.oid)
```

`RevListObjectEntry` contains the 64-hex SHA-256 object ID and the native pygit object type (`commit`, `tree`, `blob`, or `tag`). This API intentionally remains pathname-free for compatibility. Phase 121 adds the separate `pygit.rev_list_object_names` presentation API.

The operation is read-only and does not change refs, reflogs, index state, packs, or the worktree.

## Scope boundary

Phase 121 now covers pathname annotations, `--no-object-names`, `--objects-edge`, and object counting. Path limiting, reflog walks, bitmap acceleration, missing-object toleration/promisor semantics, annotated-tag input naming, and the broader native revision-option surface remain out of scope. The repository continues to use its educational SHA-256 object and pack formats.
