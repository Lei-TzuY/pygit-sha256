# Revision plumbing

`pygit rev-parse` is the script-facing revision parser for pygit's native
SHA-256 object database. Phase 57 consolidates revision resolution into
`pygit.revision`, which is also used by the advanced `cat-file` plumbing.
That keeps object names consistent across commands instead of maintaining
separate ref/SHA/path parsers. Phase 78 extends the same shared resolver with
strict numeric reflog selectors.

## Object-ish expressions

The resolver accepts:

```text
HEAD
main
refs/tags/v1
0123abcd...          # unique 4+ SHA-256 prefix
HEAD@{0}             # newest HEAD reflog value
main@{2}             # third-newest main reflog value
refs/heads/main@{1}
HEAD@{1}~2
HEAD@{1}^{tree}
HEAD@{1}:path/to/file
HEAD~2
HEAD^2
HEAD^2~1
HEAD:path/to/file
HEAD:                # root tree
v1^{}
v1^{tag}
v1^{commit}
v1^{tree}
HEAD:file.txt^{blob}
```

Numeric `REF@{N}` selectors use the same newest-first local index shown by
`pygit reflog show REF`. They resolve the selected record's `new_oid`; the
selected OID must be non-zero and still exist in loose or packed storage.
Short reflog names use the same strict normalization and path-safety rules as
Phase 77. Missing history, zero OIDs, pruned objects, malformed reflogs,
symlinked log paths, malformed selector syntax, and nested selectors fail
loudly.

Only non-negative decimal selector indices are supported. Date selectors such
as `HEAD@{yesterday}`, checkout-stack selectors such as `@{-1}`, and nested
selectors are intentionally outside the current compatibility boundary.

`~N` walks first parents and `^N` selects the Nth parent. Explicit parent
walks stop at entries in `.pygit/shallow`. Tree paths reject absolute paths,
empty components, `.` and `..` rather than silently normalizing them.

Typed peeling supports `object`, `tag`, `commit`, `tree`, and `blob`.
Annotated tags are peeled recursively where the requested type permits it.

Abbreviated object IDs are resolved against both loose objects and packfiles.
This matters after `pygit repack`, where the old loose-only prefix lookup could
no longer find an otherwise valid short SHA.

## CLI examples

```bash
pygit rev-parse HEAD HEAD~1 v1^{tree}
pygit rev-parse --verify HEAD@{1}
pygit rev-parse --verify HEAD@{1}^{tree}
pygit rev-parse --verify --quiet maybe-missing
pygit rev-parse --short=12 HEAD
pygit rev-parse --symbolic-full-name HEAD
pygit rev-parse --abbrev-ref HEAD
```

Because selector support lives in `pygit.revision`, other plumbing that uses
the shared resolver inherits it too, for example:

```bash
pygit cat-file -t 'main@{2}:README.md'
pygit ls-tree --name-only 'HEAD@{1}'
pygit rev-list 'HEAD@{3}..HEAD@{0}'
```

Namespace and argument filtering:

```bash
pygit rev-parse --branches
pygit rev-parse --branches='release/*'
pygit rev-parse --tags='v*'
pygit rev-parse --remotes='origin/*'
pygit rev-parse --glob='refs/heads/topic/*'
pygit rev-parse --all
pygit rev-parse --revs-only HEAD README.md
pygit rev-parse --no-revs HEAD README.md
pygit rev-parse --not HEAD HEAD~1
pygit rev-parse --sq HEAD
```

Repository metadata:

```bash
pygit rev-parse --show-object-format   # sha256
pygit rev-parse --show-ref-format      # files
pygit rev-parse --git-dir
pygit rev-parse --absolute-git-dir
pygit rev-parse --git-common-dir
pygit rev-parse --show-toplevel
pygit rev-parse --show-prefix
pygit rev-parse --show-cdup
pygit rev-parse --is-inside-work-tree
pygit rev-parse --is-inside-git-dir
pygit rev-parse --is-bare-repository
pygit rev-parse --is-shallow-repository
pygit rev-parse --git-path objects
pygit rev-parse --path-format=relative --git-dir
```

Object-format output is deliberately `sha256`; the loose/packed reference
backend is the files ref-storage model.

## Python API

```python
from pygit import (
    abbreviate_oid,
    resolve_abbreviation,
    resolve_revision,
    symbolic_refname,
)

oid = resolve_revision(repo, "HEAD@{1}^{tree}")
short = abbreviate_oid(repo, oid, 12)
full = symbolic_refname(repo, "main")
```

The resolver is read-only: it never changes reflogs, refs, the index, or the
worktree.
