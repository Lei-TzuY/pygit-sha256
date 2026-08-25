# Advanced merge-base graph plumbing

Phase 52 extends `pygit merge-base` from pairwise ancestry queries to Git-style
multi-commit graph operations. Phase 83 adds reflog-aware fork-point discovery.
All modes resolve commit-ish arguments through annotated tags, use pygit's
64-hex SHA-256 object IDs, and honor `.pygit/shallow` boundaries.

## Default multi-commit mode

```text
pygit merge-base A B C
pygit merge-base --all A B C
```

For more than two commits, the first commit is compared with a hypothetical
merge commit whose parents are the remaining commits. The hypothetical commit
is not written to the object database; its ancestry is represented as the union
of the remaining ancestry sets.

This is intentionally different from octopus mode. For example, if `A` descends
from `B` while `C` is on another branch, `merge-base A B C` may return `B` even
when `B` is not an ancestor of `C`.

`--all` prints every best common ancestor. This matters for criss-cross merge
histories where two or more merge bases can be equally valid.

## Octopus mode

```text
pygit merge-base --octopus A B C D
pygit merge-base --octopus --all A B C D
```

`--octopus` intersects the ancestry of every supplied commit and then removes
common ancestors that are dominated by newer common ancestors. It models the
base-selection problem for an n-way merge.

## Independent mode

```text
pygit merge-base --independent A B C D
```

`--independent` prints only supplied commits that cannot be reached from another
supplied commit. Inputs are resolved and deduplicated before reachability is
computed; output preserves the first occurrence order of surviving commits.

## Pairwise ancestry test

```text
pygit merge-base --is-ancestor A B
```

The command remains silent and exits with status 0 when `A` is an ancestor of
`B`, or status 1 otherwise. This mode requires exactly two commits.

## Reflog-aware fork point

```text
pygit merge-base --fork-point upstream topic
pygit merge-base --fork-point upstream
```

Fork-point considers the current tip plus historical tips retained in the
upstream ref's reflog. It is intended for a topic that was created from an older
incarnation of an upstream ref before that ref was rewound or rebuilt.

Unlike ordinary merge-base, a candidate is returned only when the selected best
base is itself a current or historical tip of the supplied ref. If reflog expiry
removed the relevant tip, the command exits 1 rather than returning a merely
older common ancestor. The optional topic commit defaults to `HEAD`.

## Graph algorithm

`pygit.graph_query` reuses the existing shortest-distance ancestry walk and adds
memoized topological generation numbers. Best-common-ancestor reduction visits
newer candidates first; a shared parent walk marks older common candidates as
dominated while avoiding repeated traversal of overlapping ancestry.

Fork-point composes that algorithm with the strict reflog reader: retained ref
tips are represented as a hypothetical merge and no synthetic commit object is
written. The implementation detects malformed commit-graph cycles, rejects
malformed retained reflogs, and stops parent walks at shallow boundaries.
Annotated tags are peeled before graph traversal.

## Python API

```python
from pygit import (
    fork_point,
    independent_commits,
    merge_bases_many,
    octopus_merge_bases,
)

bases = merge_bases_many(repo, ["main", "topic-a", "topic-b"])
octopus = octopus_merge_bases(repo, ["main", "topic-a", "topic-b"])
heads = independent_commits(repo, ["main", "topic-a", "topic-b"])
point = fork_point(repo, "origin/main", "topic-a")
```
