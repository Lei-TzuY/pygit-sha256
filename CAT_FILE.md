# Advanced `cat-file` plumbing

Phase 55 extends object inspection beyond the original single-object `-t`, `-s`, and `-p` modes. Phase 82 adds the interactive command-oriented batch protocol used by long-lived tooling. Phase 84 adds Git-style custom headers for every batch mode. Phase 89 adds storage-wide object enumeration.

## Object expressions

The plumbing API and advanced CLI modes accept:

- full SHA-256 object IDs
- unique object-ID prefixes
- refs such as `HEAD`, branch names, tags, and packed refs
- commit ancestry expressions such as `HEAD~2` and `topic^2`
- snapshot paths such as `HEAD:README.md` and `v1:src/main.py`
- `REV:` to address the root tree of a commit/tree-ish

`REV:path` walks tree objects without touching the worktree or index. Annotated tags are peeled only when tree traversal requires a tree-ish; plain tag inspection still addresses the tag object itself.

Index-style `:path` expressions are intentionally out of scope for this phase.

## Existence checks

```console
pygit cat-file -e HEAD
pygit cat-file -e HEAD:README.md
```

The command prints nothing and exits with status 0 when the object exists, or status 1 when it cannot be resolved.

## Batch metadata

```console
printf 'HEAD\nHEAD:README.md\nmissing\n' | pygit cat-file --batch-check
```

Each successful input produces `<object-id> <type> <size>`. Failed inputs produce `<input> missing`; failures are per-record and do not abort later records.

## Batch content

```console
printf 'HEAD:README.md\n' | pygit cat-file --batch
```

For each successful object, `--batch` emits the same metadata header followed by raw serialized content and a trailing newline.

## Custom batch formats

The optional format must be attached with `=`:

```console
printf 'HEAD metadata\n' | pygit cat-file '--batch-check=%(objectname) %(objecttype) %(objectsize) %(rest)'
printf 'HEAD:README.md\n' | pygit cat-file '--batch=%(objecttype):%(objectsize)'
printf 'info HEAD\n' | pygit cat-file '--batch-command=%(objectname)|%(objecttype)'
```

Supported atoms are `%(objectname)`, `%(objecttype)`, `%(objectsize)`, and `%(rest)`. `%%` emits a literal percent sign. Unknown or unterminated atoms fail before stdin is consumed. With `%(rest)`, ordinary batch modes split the input at the first whitespace run; command mode keeps everything after the command's first ASCII space as the object expression and expands `%(rest)` to empty.

## Batch command protocol

```console
printf 'info HEAD\ncontents HEAD:README.md\n' | pygit cat-file --batch-command
```

`info` emits metadata; `contents` emits metadata plus raw content. With `--buffer`, responses accumulate until `flush` or clean EOF.

## All-object enumeration

`--batch-all-objects` switches a batch mode from stdin-selected lookups to complete local object-store enumeration:

```console
pygit cat-file --batch-check --batch-all-objects
pygit cat-file '--batch-check=%(objectname) %(objecttype)' --batch-all-objects
pygit cat-file --batch --batch-all-objects > object-stream.bin
```

stdin is ignored completely. Enumeration includes reachable and unreachable objects, loose objects, packed-only objects, and objects duplicated between loose and packed storage. Duplicates are emitted once. Default order is deterministic lexical SHA-256 object-ID order.

Only canonical lowercase 64-hex object names are enumerated; incidental files beneath `.pygit/objects` are ignored. Object contents still pass through the ordinary verified object store, so enumeration does not weaken object integrity checks.

Custom formats remain supported and `%(rest)` expands to empty. `--batch` emits raw contents; `--batch-check` and `--batch-command` emit metadata only. `--buffer` is accepted. `--unordered` remains separate work.

## Python API

```python
from pygit import all_object_ids, batch_all_objects, format_batch_object

oids = all_object_ids(repo)
headers = list(batch_all_objects(repo))
custom = list(batch_all_objects(repo, format_string="%(objectname) %(objecttype)"))
contents = list(batch_all_objects(repo, contents=True))
header = format_batch_object(repo, "HEAD")
```

`--unordered`, symlink following, disk-size/delta-base atoms, text conversion/filters, mailmap handling, and NUL framing remain separate work.
