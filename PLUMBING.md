# Graph and reference plumbing

Phase 46 adds two low-level commands backed by reusable Python helpers in
`pygit.plumbing`.

## `pygit merge-base`

Find the best common ancestor of two commit-ish revisions:

```text
pygit merge-base main feature
pygit merge-base --all main feature
pygit merge-base --is-ancestor release HEAD
```

`--is-ancestor` is silent and uses the process status: `0` means the first
revision is an ancestor of the second, `1` means it is not. Annotated tags are
peeled to commits. Parent traversal also respects `.pygit/shallow` boundaries.

When criss-cross history has more than one best common ancestor, `--all`
prints every non-dominated candidate. Without `--all`, one deterministic
candidate is printed.

## `pygit show-ref`

Inspect the local ref namespace without walking commit history:

```text
pygit show-ref
pygit show-ref --head
pygit show-ref --heads
pygit show-ref --tags
pygit show-ref main
pygit show-ref --verify refs/heads/main
pygit show-ref --verify --quiet refs/tags/v1.0
pygit show-ref --dereference --tags
```

Patterns match complete suffix path components, following Git's `show-ref`
style: `main` matches both `refs/heads/main` and `refs/remotes/origin/main`, but
not an unrelated ref containing `main` as a substring. `--verify` requires
fully-qualified `refs/...` names. `--dereference` emits the peeled target of
annotated tag refs using the conventional `^{}` suffix.

Malformed ref files are rejected instead of being silently ignored. Object IDs
remain pygit's native 64-hex SHA-256 identifiers.

## Python API

```python
from pygit.plumbing import is_ancestor, list_refs, merge_bases

bases = merge_bases(repo, "main", "feature")
contained = is_ancestor(repo, "v1.0", "HEAD")
refs = list_refs(repo, heads=True)
```

These helpers intentionally live outside the large porcelain `Repository`
implementation so graph/ref logic can be tested and extended independently.
