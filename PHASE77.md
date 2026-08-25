# Phase 77: strict reflog inspection

Phase 77 upgrades the historical read-only `reflog` display path into a reusable inspection layer while preserving Phase 72 `reflog expire` semantics.

## CLI

Both the historical shorthand and the explicit nested form are supported:

```bash
pygit reflog
pygit reflog HEAD
pygit reflog main
pygit reflog show
pygit reflog show refs/heads/main
pygit reflog show --all
pygit reflog show -n 20 --reverse
pygit reflog show --format '%gD %H %ct %gs'
```

The default format remains compatible with the previous handler:

```text
<12-char-new-oid> <ref>@{<index>}: <message>
```

Short branch and remote-tracking names are resolved only when an existing reflog file makes the name unambiguous. A missing short name preserves the legacy empty-result behaviour instead of being interpreted as an object revision.

`--all` scans every regular reflog below `.pygit/logs`, rejects symlinked entries, and globally orders records by timestamp. Selector indices remain local to each ref and are always counted newest-first. `-n/--max-count` is applied before `--reverse`.

## Strict parsing

Inspection deliberately reuses the same strict parser and path-selection primitives used by Phase 72 `reflog expire`. Existing malformed logs therefore fail loudly for:

- malformed or non-SHA-256 object IDs;
- malformed or negative timestamps;
- malformed timezone fields;
- invalid UTF-8;
- symlinked reflog files or entries that would escape `.pygit/logs`.

Showing a reflog never requires its historical objects to remain present. The command is inspecting recovery metadata, so unreachable or already-pruned OIDs are still printable when the reflog record itself is structurally valid.

## Formatting

`--format` supports a deliberately small, documented placeholder set:

- `%H`: full new object ID
- `%h`: 12-character new object ID
- `%o`: full old object ID
- `%gD`: selector such as `HEAD@{0}`
- `%gs`: reflog message
- `%ct`: timestamp
- `%r`: displayed ref name
- `%%`: literal percent sign

Unknown placeholders are rejected instead of being copied silently.

## Python API

```python
from pygit import format_reflog_entry, show_reflog

entries = show_reflog(repo, "main", max_count=10)
for entry in entries:
    print(format_reflog_entry(entry, "%gD %H %gs"))
```

`ReflogShowEntry`, `normalize_reflog_ref()`, `show_reflog()`, and `format_reflog_entry()` are public APIs. They do not modify refs, objects, the index, the worktree, or reflog bytes.

## Scope boundary

This phase does not implement reflog deletion, selector-based revision resolution (`HEAD@{N}` as an object revision), date-expression selection, native Git pretty-format parity, or reflog mutation beyond the already merged Phase 72 expiry plumbing.
