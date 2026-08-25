# Phase 78: Numeric reflog revision selectors

Phase 78 connects Phase 77's strict reflog reader to the shared revision resolver. Numeric reflog selectors are therefore object-ish expressions rather than a `rev-parse`-only special case.

## Supported selectors

The resolver accepts a non-negative numeric selector after an explicit reflog name:

```text
HEAD@{0}
HEAD@{1}
main@{2}
refs/heads/main@{3}
origin/main@{1}
```

The selector index is the same newest-first index shown by `pygit reflog show`: `@{0}` is the newest record, `@{1}` is the previous record, and so on. The selected object is that record's `new_oid`, representing the ref state after the recorded update.

Short names use Phase 77 normalization. Existing branch and remote reflogs may therefore be addressed by their short names, while ambiguous names fail loudly.

## Shared composition

Because selector resolution lives below the existing ancestry, peeling, and tree-path parser, selectors compose with the rest of the revision grammar:

```bash
pygit rev-parse 'HEAD@{1}'
pygit rev-parse 'HEAD@{2}~1'
pygit rev-parse 'main@{1}^{tree}'
pygit cat-file -t 'HEAD@{2}:README.md'
pygit ls-tree --name-only 'HEAD@{1}'
```

Any command already using `pygit.revision.resolve_revision()` inherits the capability without a command-specific parser or routing change.

## Validation and safety

Selector lookup reuses the strict Phase 77 reflog reader and the Phase 72 path parser. In particular:

- malformed reflog records still fail loudly;
- unsafe or symlinked reflog paths remain fail-closed, including short-name lookup;
- a selector outside the available reflog range is rejected;
- a selector whose `new_oid` is the all-zero deletion sentinel is rejected;
- a selector whose referenced object is no longer present in either loose or packed storage is rejected;
- packed-only historical objects remain valid because object existence uses the normal packed-aware object store;
- selector resolution is read-only and does not change refs, reflogs, the index, objects, or the worktree.

`symbolic_refname()` deliberately reports no symbolic name for a reflog selector. A selector denotes a historical object state, not the current symbolic ref target.

## Deliberate scope boundary

This phase does not implement every native Git reflog-selector form. The following remain out of scope:

- date expressions such as `HEAD@{yesterday}`;
- upstream/push selectors such as `@{upstream}` or `@{push}`;
- checkout-stack selectors such as `@{-1}`;
- shorthand selectors without an explicit ref name;
- reflog walking in `rev-list`.

Those forms have different semantics and should not be approximated by silently treating them as numeric selectors.

## Regression coverage

Phase 78 covers:

- `HEAD`, short branch, and fully-qualified ref selectors;
- ancestry, typed peeling, and `REV:path` composition;
- inherited CLI behavior through `rev-parse`, `cat-file`, and modern `ls-tree`;
- packed-only historical objects after repack;
- out-of-range and malformed selector syntax;
- zero/missing historical objects;
- short-name symlink/path safety inherited from Phase 77;
- Python 3.9 and 3.13 compatibility through the full test matrix.
