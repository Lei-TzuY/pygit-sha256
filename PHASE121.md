# Phase 121 — rev-list object names and boundary edges

Phase 121 completes the next native-style layer on top of Phase 75's object-closure selection. The underlying `rev_list_objects()` API keeps its original OID/type contract; the installed CLI now adds pathname decoration, OID-only suppression, object counting, and `--objects-edge` boundary reporting without changing the selected object set.

## Commands

```bash
pygit rev-list --objects HEAD
pygit rev-list --objects --no-object-names HEAD
pygit rev-list --objects --count HEAD
pygit rev-list --objects-edge release..HEAD
pygit rev-list --objects-edge --no-object-names -n 1 release..HEAD
```

`--objects-edge` implies object enumeration. `--no-object-names` is meaningful only for the object stream and is harmless otherwise. The existing mixed `--left-right` object protocol remains rejected because pygit's Phase 68 side-marker API and the named-object stream are still deliberately separate.

## Pathname annotations

Selected commits are emitted first in normal `rev-list` order and have no pathname. Their selected tree closure is then traversed pre-order in deterministic tree-name order.

- the root tree uses the empty pathname and is rendered as `OID SP`;
- nested trees use directory paths such as `src`;
- blobs use paths such as `src/main.py`;
- if the same object is reachable under multiple names, only the first deterministic pathname is retained;
- objects removed by Phase 75 negative/common-ancestry subtraction never receive a pathname because they are not in the selected set.

The traversal preserves Phase 75's exact object set. Unusual selected objects that do not have an ordinary tree pathname are retained at the end without inventing one.

## `--no-object-names`

Native-style pathname decoration is now the default CLI presentation for `--objects`. `--no-object-names` suppresses the suffixes and emits only OIDs while preserving the same named traversal order. The original Python `rev_list_objects()` API remains unchanged for callers that rely on its Phase 75 deterministic OID/type representation.

## Object counting

`--objects --count` now counts the selected object stream rather than being rejected. The count includes selected commits, trees, blobs, and any other selected object entries. `--objects-edge --count` emits boundary edge lines first and then the object count; boundary markers are not included in the count.

## Boundary edges

`--objects-edge` prefixes each uninteresting boundary commit with `-` before the object stream. An edge is a parent immediately outside the complete selected commit set.

Edge discovery is performed before `--skip` and `--max-count`. Therefore a range boundary remains advertised even when the visible commit stream is later limited. Unrelated negative roots are not emitted merely because they appeared on the command line; they must actually border the selected history. A symmetric range reports its common-parent boundary in the same way.

## Python API

```python
from pygit.rev_list_object_names import (
    RevListNamedObjectEntry,
    rev_list_named_objects,
    rev_list_object_edges,
)

entries = rev_list_named_objects(repo, ["release..HEAD"], topo_order=True)
edges = rev_list_object_edges(repo, ["release..HEAD"])
```

`RevListNamedObjectEntry.path` is `None` for commits or objects without a tree pathname, `""` for a root tree, and otherwise the first deterministic repository-relative tree pathname.

## Safety and compatibility

All operations are read-only. No refs, reflogs, index entries, loose objects, packs, or worktree paths are modified. Selection still delegates to Phase 68/75 commit and object traversal, including shallow boundaries, negative closures, symmetric ranges, first-parent behavior, ordering, skip, and max-count semantics.

Path limiting, reflog walks, bitmap acceleration, promisor/missing-object toleration, annotated-tag input naming, and the wider native Git revision-option surface remain separate future work.
