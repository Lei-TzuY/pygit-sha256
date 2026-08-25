# Advanced merge-base graph plumbing

Phase 52 extends `pygit merge-base` from pairwise ancestry queries to Git-style
multi-commit graph operations. Phase 81 additionally connects merge-base to the
strict reflog reader for rewrite-aware fork-point discovery. All graph modes use
pygit's 64-hex SHA-256 object IDs and honor `.pygit/shallow` boundaries.

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
pygit merge-base --fork-point upstream
pygit merge-base --fork-point upstream topic
```

`--fork-point` is useful when `upstream` was rewritten after `topic` forked.
Instead of relying only on the current upstream graph, it inspects the strict
reflog history and finds historical upstream tips that are ancestors of the
derived commit. Older eligible candidates dominated by newer eligible
candidates are removed; exactly one best candidate is required.

The derived commit defaults to `HEAD` and uses the shared revision resolver, so
reflog selectors such as `HEAD@{0}` and packed-only commit objects work here as
they do in other modern plumbing commands.

If there is no reflog evidence, no eligible historical state, or more than one
incomparable best historical state, the mode prints nothing and exits 1.
Malformed/unsafe reflogs fail loudly. Missing old objects that have already been
pruned are ignored, which can make a previously discoverable fork point become
unavailable.

## Graph algorithm

`pygit.graph_query` reuses the existing shortest-distance ancestry walk and adds
memoized topological generation numbers. Best-common-ancestor reduction visits
newer candidates first; a shared parent walk marks older common candidates as
dominated while avoiding repeated traversal of overlapping ancestry.

`pygit.fork_point` uses the same ancestry walk on the current ref tip plus
historical reflog states. It keeps only candidates reachable from the derived
commit and performs an analogous dominance reduction before requiring a unique
result.

The implementation detects malformed commit-graph cycles and stops parent walks
at shallow boundaries. Annotated tags are peeled before graph traversal.

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
base = fork_point(repo, "upstream", "topic")
```
