# Phase 68: advanced rev-list plumbing

Phase 68 replaces the early single-start `rev-list` path with a reusable commit-set traversal engine while keeping the repository read-only.

## Supported selection

```bash
pygit rev-list HEAD
pygit rev-list main topic
pygit rev-list HEAD ^release
pygit rev-list release..HEAD
pygit rev-list left...right
pygit rev-list --left-right left...right
pygit rev-list --all
```

Positive revisions contribute their reachable commits. `^REV` subtracts `REV` and its reachable ancestry. `A..B` is therefore equivalent to `B ^A`. A single `A...B` selects the symmetric difference of the two ancestry sets; `--left-right` prefixes those commits with `<` or `>`.

Phase 131 extends that symmetric-range presentation with `--left-only` / `--right-only` and native-style two-column `--left-right --count` output while leaving the Phase 68 graph engine unchanged.

Annotated tags are peeled to commits. `--all` starts from every commit-ish loose or packed ref while ignoring refs whose final object is not a commit. Traversal respects `.pygit/shallow` boundaries.

## Ordering and limiting

`rev-list` uses deterministic committer-date order by default. Additional controls are:

- `--topo-order`: selected children always appear before selected parents, with committer time as a deterministic tie-break.
- `--first-parent`: only the first parent of each merge is traversed.
- `--skip N`: omit the first N selected commits.
- `-n N` / `--max-count N`: cap the selected output.
- `--reverse`: reverse the selected output after skip/max-count are applied.
- `--count`: print the final selected count; with Phase 131 `--left-right`, print left and right counts as two tab-separated columns.

For Phase 131 side filters, left/right filtering occurs before skip/max-count and reverse remains the final presentation transform.

## Python API

```python
from pygit.rev_list import RevListEntry, rev_list

entries = rev_list(
    repo,
    ["release..HEAD"],
    topo_order=True,
    max_count=50,
)
for entry in entries:
    print(entry.side or "", entry.oid)
```

`rev_list()` returns immutable `RevListEntry` records and does not modify refs, the index, the worktree, or the object database. Phase 131's focused side filtering/count helpers live separately in `pygit.rev_list_sides`.

## Scope boundary

Phase 68 intentionally focused on commit-set graph semantics. Phase 75 subsequently adds OID-only object enumeration via `rev-list --objects` and `rev_list_objects()`. Phase 121 adds pathname annotations and boundary edges. Path limiting, reflog walks, bitmap acceleration, and the broader native Git revision-option surface remain out of scope. The repository continues to use its SHA-256 object model rather than claiming native Git object-format compatibility.
