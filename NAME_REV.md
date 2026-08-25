# Commit naming plumbing

Phase 53 adds read-only symbolic naming for commits.  It complements the graph
queries in `GRAPH_PLUMBING.md`: merge-base answers reachability questions,
while `name-rev` turns raw SHA-256 commit IDs back into useful ref-relative
names.

## CLI

```text
pygit name-rev COMMIT...
pygit name-rev --name-only COMMIT...
pygit name-rev --all
pygit name-rev --tags COMMIT...
pygit name-rev --refs 'refs/heads/release/*' COMMIT...
pygit name-rev --always COMMIT...
pygit name-rev --no-undefined COMMIT...
```

Examples of generated names are:

```text
main
main~3
main^2
main^2~4
tags/v2.0^0
tags/v2.0^0~2
remotes/origin/main~1
```

Consecutive first-parent edges are compacted into `~N`.  A non-first parent is
written as `^N`, where `^2` means the second parent.  Annotated tags are peeled
to commits and shown with an explicit `^0`; lightweight tags need no peel
marker.

## Naming anchors and ranking

By default local branches, tags, remote-tracking refs and other usable refs are
considered.  `--tags` restricts anchors to `refs/tags/*`.  Repeated `--refs`
patterns filter anchors with shell-style globs and can match either the fully
qualified ref name or its displayed name.

For one commit, the selected name is deterministic.  Shorter ancestry paths
win first.  At equal distance, tags are preferred over local branches, then
remote-tracking refs, then other namespaces.  Shorter textual names are used
as a final tie-breaker.

Symbolic refs under `refs/` are dereferenced when possible.  Broken refs,
malformed object IDs and refs whose peeled target is not a commit are ignored;
they do not make an unrelated naming query fail.

## Shallow history

`.pygit/shallow` entries are treated as graph roots.  Name propagation never
crosses a shallow boundary, so the command does not invent ancestry that the
local repository intentionally does not expose.

## Undefined commits

A valid commit may exist in the object database without being reachable from
any selected ref.  Default output calls it `undefined`.

- `--always` falls back to the first 12 hexadecimal characters of the SHA-256
  object ID.
- `--no-undefined` fails instead of emitting an undefined name.

These two modes are mutually exclusive.

## Python API

```python
from pygit import name_all, name_revision, name_revisions

one = name_revision(repo, "HEAD~2")
many = name_revisions(repo, ["main", "feature"])
all_named = name_all(repo, tags_only=True)
```

Each result is a `NameRevResult` containing the original revision string, the
resolved 64-hex commit ID, and an optional symbolic name.  The API and CLI are
read-only: they never alter refs, reflogs, the index, or the working tree.
