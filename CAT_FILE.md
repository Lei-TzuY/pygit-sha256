# Advanced `cat-file` plumbing

Phase 55 extends object inspection beyond the original single-object `-t`, `-s`, and `-p` modes. Phase 82 adds the interactive command-oriented batch protocol used by long-lived tooling. Phase 84 adds Git-style custom headers for every batch mode.

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

Each successful input produces:

```text
<object-id> <type> <size>
```

A failed input produces:

```text
<input> missing
```

Batch failures are per-record and do not abort later records.

## Batch content

```console
printf 'HEAD:README.md\n' | pygit cat-file --batch
```

For each successful object, `--batch` emits the same metadata header followed by the object's raw serialized content and a trailing newline. This makes the mode useful for scripts that need to inspect many objects without starting one process per lookup.

## Custom batch formats

The optional format must be attached with `=` just like Git:

```console
printf 'HEAD metadata\n' | \
  pygit cat-file '--batch-check=%(objectname) %(objecttype) %(objectsize) %(rest)'

printf 'HEAD:README.md\n' | \
  pygit cat-file '--batch=%(objecttype):%(objectsize)'

printf 'info HEAD\ncontents HEAD:README.md\n' | \
  pygit cat-file '--batch-command=%(objectname)|%(objecttype)|%(objectsize)'
```

Supported atoms are `%(objectname)`, `%(objecttype)`, `%(objectsize)`, and `%(rest)`. `%%` emits a literal percent sign; other percent sequences are preserved literally. Unknown or unterminated atoms fail before stdin is consumed, preventing partial output from an invalid format.

When `%(rest)` is present in `--batch` or `--batch-check`, the first whitespace run separates the object expression from auxiliary text; the separator is removed and the remaining text is preserved verbatim. Without `%(rest)`, the complete input line remains the object expression, so paths such as `HEAD:a b.txt` continue to work. `--batch-command` does not apply the rest split: everything after the command's first ASCII space remains the object expression and `%(rest)` expands to an empty string.

Custom formatting applies only to successful headers. Missing objects always retain the stable `<object> missing` record so scripts can recognize failures independently of the selected format. An empty custom format is valid and emits only the record newline.

## Batch command protocol

```console
printf 'info HEAD\ncontents HEAD:README.md\n' | pygit cat-file --batch-command
```

`info <object>` behaves like one `--batch-check` request. `contents <object>` behaves like one `--batch` request. Missing object expressions emit `<object> missing` and processing continues.

With `--buffer`, responses are accumulated until a `flush` command is received. Pending output is also emitted at clean end-of-input. A parse error before `flush` does not publish the pending buffered data. Without `--buffer`, each response is flushed immediately for interactive clients.

The command delimiter is one ASCII space; everything after that first space belongs to the object expression, so additional leading spaces are preserved rather than normalized.

## Python API

```python
from pygit import (
    format_batch_object,
    format_batch_record,
    inspect_object,
    object_exists,
    parse_batch_command,
    resolve_object,
    run_batch_commands,
    split_batch_input,
)

oid = resolve_object(repo, "HEAD:README.md")
record = inspect_object(repo, "HEAD:README.md")
assert record.oid == oid
assert record.type_name == "blob"
assert object_exists(repo, "HEAD")

expression, rest = split_batch_input(
    "HEAD metadata\n",
    "%(objectname) %(rest)",
)
custom = format_batch_record(record, "%(objecttype) %(objectsize)")
command = parse_batch_command("info HEAD\n")
chunks = list(
    run_batch_commands(
        repo,
        ["info HEAD\n", "flush\n"],
        buffered=True,
        format_string="%(objectname) %(objecttype)",
    )
)
header = format_batch_object(repo, "HEAD")
```

All-object enumeration, `--unordered`, symlink following, `objectsize:disk`/delta-base atoms, text conversion/filters, mailmap toggling, and NUL-framed input remain separate work rather than being approximated here.
