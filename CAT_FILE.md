# Advanced `cat-file` plumbing

Phase 55 extends object inspection beyond the original single-object `-t`, `-s`, and `-p` modes. Phase 82 adds the interactive command-oriented batch protocol used by long-lived tooling.

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
    inspect_object,
    object_exists,
    parse_batch_command,
    resolve_object,
    run_batch_commands,
)

oid = resolve_object(repo, "HEAD:README.md")
record = inspect_object(repo, "HEAD:README.md")
assert record.oid == oid
assert record.type_name == "blob"
assert object_exists(repo, "HEAD")

command = parse_batch_command("info HEAD\n")
chunks = list(run_batch_commands(repo, ["info HEAD\n", "flush\n"], buffered=True))
header = format_batch_object(repo, "HEAD")
```

The original single-object and Phase 55 batch modes remain supported. Custom batch formatting, all-object enumeration, symlink following, text conversion/filters, mailmap toggling, and NUL-framed command input are outside the Phase 82 scope.
