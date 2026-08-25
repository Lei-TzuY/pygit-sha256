# Object construction plumbing

Phase 50 completes the low-level path from the staging index to immutable tree
and commit objects. These commands create objects only: they do not update HEAD,
branches, reflogs, or run porcelain commit hooks.

## `pygit write-tree`

Write the current index as a hierarchy of SHA-256 tree objects and print the
root tree object ID:

```text
pygit write-tree
pygit write-tree --prefix=src
pygit write-tree --missing-ok
```

Before writing, pygit validates every index path, detects file/directory path
collisions, checks supported modes, verifies full SHA-256 object IDs, and checks
that regular/executable/symlink entries reference blobs while gitlinks
(`160000`) reference commits. `--missing-ok` permits intentionally absent
objects while retaining all structural validation.

`--prefix=DIR` selects only index entries below that directory and strips the
prefix from the returned subtree. The index itself is never changed. An empty
index produces the canonical empty tree object.

## `pygit commit-tree`

Create a commit around an existing tree without changing any ref:

```text
pygit commit-tree <tree> -m "initial object commit"
pygit commit-tree <tree> -p HEAD -m "child"
printf 'message\n' | pygit commit-tree <tree>
pygit commit-tree <tree> -F message.txt
```

`-p/--parent` may be supplied multiple times and accepts pygit commit-ish
revisions. Parents are validated as commits and duplicate parents are rejected.
The tree argument must resolve to an actual tree object; passing a blob or commit
object is an error.

Message paragraphs may come from repeated `-m`, from `-F FILE`, from `-F -`, or
from stdin when neither option is supplied. Multiple message sources are joined
with a blank line.

Identity follows the conventional environment variables:

```text
GIT_AUTHOR_NAME
GIT_AUTHOR_EMAIL
GIT_AUTHOR_DATE
GIT_COMMITTER_NAME
GIT_COMMITTER_EMAIL
GIT_COMMITTER_DATE
```

Dates accept Unix timestamps with an optional `+HHMM`/`-HHMM` timezone, or ISO
8601 values. Missing committer name/email fall back to the author; otherwise the
educational defaults are `Unknown <unknown@example.com>`.

Because commit objects are content-addressed, identical tree, parents, identity,
dates, and message produce the same SHA-256 object ID.

## Python API

```python
from pygit.commit_plumbing import commit_tree, write_tree

tree_oid = write_tree(repo)
commit_oid = commit_tree(
    repo,
    tree_oid,
    parents=["HEAD"],
    message="plumbing commit",
)
```

This layer intentionally reuses the repository's existing recursive tree builder
while adding strict validation and no-ref-mutation semantics around it.
