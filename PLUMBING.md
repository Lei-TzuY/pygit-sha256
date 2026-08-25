# Graph and reference plumbing

Phase 46 adds two low-level commands backed by reusable Python helpers in
`pygit.plumbing`. Phase 47 extends the same layer with structured ref querying,
formatting, graph filters, and refname validation in `pygit.ref_query`.

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

## `pygit for-each-ref`

Query the ref namespace as structured data rather than fixed `show-ref` lines:

```text
pygit for-each-ref
pygit for-each-ref refs/heads/
pygit for-each-ref --sort=-refname --count=5
pygit for-each-ref --format="%(refname:short) %(objectname:short) %(subject)"
pygit for-each-ref --contains=v1.0 refs/heads/
pygit for-each-ref --merged=main refs/heads/
pygit for-each-ref --no-merged=main refs/heads/
```

Literal patterns select a full-ref prefix; patterns containing `*`, `?`, or
`[` use shell-style matching. Multiple `--sort` options are stable and the last
key is primary, matching Git's ordering model. Supported sort keys are
`refname`, `objectname`, `objecttype`, `authordate`, `committerdate`,
`taggerdate`, and `creatordate`; prefix a key with `-` for descending order.

The formatter implements the useful core atoms: `refname`, `refname:short`,
`objectname`, `objectname:short[=N]`, `objecttype`, `subject`,
`contents:subject`, author/committer/tagger/creator name and email fields, and
Unix date fields. `%09`, `%0a`, and similar hex escapes can be used as
separators. Graph predicates peel annotated tags before walking commit ancestry.

## `pygit check-ref-format`

Validate names before using them as refs or branches:

```text
pygit check-ref-format refs/heads/topic
pygit check-ref-format --branch topic
pygit check-ref-format --allow-onelevel FETCH_HEAD
pygit check-ref-format --normalize //refs//heads/topic
```

Validation rejects empty path components, dot-prefixed components, `.lock`
suffixes, `..`, `@{`, control characters, spaces, and Git's reserved ref
punctuation (`~^:?*[\\`). One-level names are rejected unless explicitly
allowed; `--branch` permits one-level branch names but rejects a leading `-`.
`--normalize` removes leading/repeated slashes, validates the result, and prints
the normalized refname.

## Python API

```python
from pygit.plumbing import is_ancestor, list_refs, merge_bases
from pygit.ref_query import check_ref_format, format_ref, query_refs

bases = merge_bases(repo, "main", "feature")
contained = is_ancestor(repo, "v1.0", "HEAD")
refs = list_refs(repo, heads=True)

records = query_refs(repo, patterns=["refs/heads/"], sort_keys=["-refname"])
line = format_ref(records[0], "%(refname:short) %(subject)")
checked = check_ref_format("refs/heads/topic")
```

These helpers intentionally live outside the large porcelain `Repository`
implementation so graph/ref logic can be tested and extended independently.
