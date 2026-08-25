# Graph, reference, tree, and index plumbing

Phase 46 adds low-level graph/reference commands backed by reusable Python
helpers in `pygit.plumbing`. Phase 47 extends the same layer with structured ref
querying, formatting, graph filters, and refname validation in
`pygit.ref_query`. Phase 48 adds tree-object construction and index loading in
`pygit.tree_plumbing`. Phase 49 adds direct index mutation and inspection in
`pygit.index_plumbing`.

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

## `pygit mktree`

Build a SHA-256 tree object directly from `ls-tree`-style records supplied on
standard input:

```text
100644 blob <64-hex-oid>\tREADME.md
040000 tree <64-hex-oid>\tsrc
```

```text
pygit mktree < entries.txt
pygit mktree -z < entries.nul
pygit mktree --missing < entries.txt
```

Each record is validated as `MODE TYPE OID<TAB>NAME`. Supported modes are
regular/executable blobs (`100644`/`100755`), symlinks (`120000`), trees
(`040000`), and gitlinks (`160000`). Mode/type mismatches, duplicate names,
invalid path components, malformed SHA-256 IDs, and missing objects are rejected.
`--missing` permits references to objects that are intentionally absent.

## `pygit read-tree`

Load a tree-ish into the staging index without creating a commit:

```text
pygit read-tree HEAD
pygit read-tree HEAD~1
pygit read-tree --empty
pygit read-tree --prefix=vendor/lib third-party-tag
pygit read-tree -u release
```

A tree-ish may be a tree object, commit, annotated tag, or parent-walking
revision. The default operation replaces only the index; it does not modify the
working tree. `--prefix` adds the flattened tree below a prefix while preserving
existing index entries and rejects file/path collisions. `-u` also materializes
the selected tree, but first requires a clean repository so local changes are
not overwritten. `--empty` clears the index and, with `-u`, removes currently
tracked worktree paths.

Tree traversal verifies every entry's mode/object-type relationship before the
index is written, so malformed trees are rejected rather than silently staged.

## `pygit update-index`

Manipulate the staging index directly without porcelain `add`/`rm` behavior:

```text
pygit update-index tracked.txt
pygit update-index --add new.txt
pygit update-index --remove deleted.txt
pygit update-index --force-remove generated.txt
pygit update-index --chmod=+x scripts/tool
pygit update-index --cacheinfo 100644 <blob-oid> virtual.txt
pygit update-index --index-info < index.records
pygit update-index --stdin < paths.txt
pygit update-index --refresh
```

Existing tracked paths are rehashed from the worktree. New paths require
`--add`; missing tracked paths require `--remove`; `--force-remove` drops an
entry even if the worktree file remains. Symlinks are stored as link-target
bytes with mode `120000`, and `--chmod` changes only the index mode.

`--cacheinfo` inserts an existing blob/commit object by mode and object ID;
`--index-info` accepts `MODE OBJECT [STAGE]<TAB>PATH` records and mode `0` removes
an entry. Pygit's index currently represents only stage 0, so higher unmerged
stages are rejected explicitly. Object type, path traversal, `.pygit` metadata,
and file/directory index collisions are validated before the index is saved.

`--refresh` does not stage content. It only refreshes cached stat metadata for
entries whose current blob and mode still match, printing `needs update` and
returning status 1 for dirty/deleted entries.

## `pygit ls-files`

Inspect the index and its relationship to the working tree:

```text
pygit ls-files
pygit ls-files --stage
pygit ls-files --deleted
pygit ls-files --modified
pygit ls-files --stage src/
pygit ls-files --error-unmatch README.md
pygit ls-files -z
```

The default is cached index paths. `--stage` emits
`MODE OBJECT 0<TAB>PATH`; `--deleted` selects tracked paths absent from the
worktree; `--modified` selects tracked paths whose blob content or mode differs.
Literal path arguments match exact paths or directory prefixes, while `*`, `?`,
and `[` enable shell-style matching. `-z` emits NUL-delimited records.

## Python API

```python
from pygit.index_plumbing import ls_files, refresh_index, update_index
from pygit.plumbing import is_ancestor, list_refs, merge_bases
from pygit.ref_query import check_ref_format, format_ref, query_refs
from pygit.tree_plumbing import make_tree, read_tree, resolve_treeish

bases = merge_bases(repo, "main", "feature")
contained = is_ancestor(repo, "v1.0", "HEAD")
refs = list_refs(repo, heads=True)

records = query_refs(repo, patterns=["refs/heads/"], sort_keys=["-refname"])
line = format_ref(records[0], "%(refname:short) %(subject)")
checked = check_ref_format("refs/heads/topic")

tree_oid = make_tree(repo, [f"100644 blob {blob_oid}\tREADME.md"])
read_tree(repo, tree_oid)
resolved_tree = resolve_treeish(repo, "HEAD~1")

update_index(repo, ["README.md"])
dirty = refresh_index(repo)
staged = ls_files(repo, stage=True)
```

These helpers intentionally live outside the large porcelain `Repository`
implementation so low-level mechanisms can be tested and extended independently.
