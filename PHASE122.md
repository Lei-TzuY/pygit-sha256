# Phase 122 — `ls-tree -l/--long` object sizes

Phase 122 adds Git-style long-form tree listings without changing Phase 76 traversal or default formatting.

## Commands

```bash
pygit ls-tree -l HEAD
pygit ls-tree --long --abbrev=12 HEAD
pygit ls-tree -lrtz HEAD
```

`-l` / `--long` is an output mode. Like native Git, it is mutually exclusive with `--name-only`, `--object-only`, and `--format`.

## Record shape

Long mode emits:

```text
<mode> <type> <object-id> <size-padded><TAB><path>
```

The size column is right-aligned to seven characters, matching native Git's `%(objectsize:padded)` presentation.

- regular/executable file blobs report their raw blob payload size;
- symlinks are blobs and therefore report the byte length of the symlink target stored in the object database;
- tree entries report `-`;
- gitlink (`160000`, type `commit`) entries report `-`.

Tree and gitlink sizes do not require reading the referenced leaf object. Blob sizes do require the object to exist and deserialize as a blob; a malformed mode/type pairing fails rather than printing a fabricated size.

## Composition

Long output composes with the existing Phase 76 behavior:

- `-r` recursive traversal;
- `-t` tree records while recursing;
- `-d` directory-only selection;
- pathspec filtering;
- `--abbrev[=N]` uniqueness-aware SHA-256 abbreviation;
- `-z` NUL record termination;
- loose and packed object storage.

The implementation is presentation-only: traversal remains in `pygit.ls_tree`, while `pygit.ls_tree_long` formats the selected structured entries. Existing `format_ls_tree()` callers retain their previous output semantics.

## Verification

`tests/test_phase122.py` covers exact blob/symlink/tree/gitlink size records, recursive tree output, abbreviation, NUL framing, packed-only blob size lookup, missing gitlinks, malformed blob entries, installed CLI/help, and output-mode exclusivity.
