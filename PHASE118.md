# Phase 118 — stage-zero index object resolution

Phase 118 extends the shared revision parser with Git-style object expressions
that address the staging index directly.

## Supported expressions

```bash
pygit rev-parse --verify :README.md
pygit rev-parse --verify :0:README.md
pygit cat-file -p :README.md
pygit cat-file -t ':README.md^{blob}'
```

`:<path>` and `:0:<path>` both select stage 0. The returned SHA-256 object ID
comes from `.pygit/index`, not from `HEAD`, so staged content can be inspected
without creating a tree or commit first.

## Path semantics

Plain paths are repository-root-relative. Git's leading-dot forms are also
supported:

- `:./file` resolves relative to the current working directory;
- `:../file` may walk toward the repository root from a subdirectory;
- cwd-relative normalization is rejected if it would escape the repository.

Only the prefixes `:0:`, `:1:`, `:2:`, and `:3:` have stage syntax. Other
colons remain part of the path. For example `:4:a` addresses a literal stage-0
index path named `4:a`, while `:0:0:a` explicitly addresses stage 0 of `0:a`.

## Conflict-stage evolution

At the Phase 118 boundary, pygit's readable JSON index stored one `IndexEntry`
per path and had no representation for Git's unmerged stages 1, 2, and 3. The
phase therefore rejected `:1:path`, `:2:path`, and `:3:path` explicitly rather
than inventing synthetic conflict data.

Phase 122 later upgrades that same index model with persistent stages 1-3 and
wires them through `update-index --index-info`, `ls-files --stage`, and the
shared revision resolver. The Phase 118 grammar and stage-0 behavior stay
unchanged; the formerly unavailable conflict-stage expressions now resolve real
stored entries.

## Shared resolver composition

The syntax lives behind `pygit.revision.resolve_revision()`, so every plumbing
command already using the unified resolver inherits index lookup. Typed peeling
continues to work, including `:path^{object}` and `:path^{blob}`. Existing
`REV:path` tree traversal is unchanged and remains a separate namespace.

A missing index path/stage or an index entry whose backing object is absent
fails rather than returning an unusable object ID. Resolution is read-only and
does not modify the index, worktree, refs, or reflogs.

## Regression coverage

`tests/test_phase118.py` covers the original stage-0 contract:

- staged objects remaining distinct from the same path in `HEAD`;
- implicit and explicit stage 0;
- colon-containing index paths and Git's stage-prefix disambiguation;
- cwd-relative `./` and `../` forms plus repository-boundary rejection;
- the original explicit failure boundary for unavailable stages 1-3;
- typed peeling of index-selected blobs;
- missing index entries and missing backing objects;
- installed `rev-parse` and `cat-file` sharing the same resolution path.

Phase 122 adds the regression suite for actual stages 1-3.
