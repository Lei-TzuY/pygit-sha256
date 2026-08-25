# Phase 79 — strict local `show-ref` plumbing

Phase 79 adds a read-only, script-facing view of the local reference namespace.
It composes the strict loose/packed ref primitives introduced by earlier phases
rather than maintaining a second ref parser.

## Public API

```python
from pygit import ShowRefEntry, format_show_refs, show_refs
```

`show_refs()` returns structured `ShowRefEntry` records. Normal enumeration:

- merges loose and packed refs with loose refs shadowing packed records;
- excludes `HEAD` unless `include_head=True`;
- supports branch/tag namespace filtering;
- uses Git-style tail-component pattern matching (`main` can match both
  `refs/heads/main` and `refs/remotes/origin/main`);
- preserves deterministic ref ordering;
- does not require the referenced object to exist merely to display a direct
  ref.

Exact verification is available through `verify_refs=(...)`. Each requested
name must be a fully-qualified `refs/...` path and must exist in the local ref
namespace. Malformed loose or packed refs fail closed.

With `dereference=True`, annotated tag refs gain a synthetic `ref^{}` record for
the fully peeled target. Lightweight tags do not gain an extra record. A tag
object whose target is broken remains inspectable in normal mode but fails when
peeling is explicitly requested.

`format_show_refs()` supports full object IDs, hash-only output, fixed
`--hash=N` prefixes, and uniqueness-aware `--abbrev=N` object names. pygit's
local repository format continues to use 64-hex SHA-256 object IDs.

## CLI

```text
pygit show-ref [--head] [--branches|--heads] [--tags]
               [-d|--dereference] [-s] [--hash[=N]] [--abbrev[=N]]
               [PATTERN...]

pygit show-ref --verify [-q|--quiet] [-d|--dereference]
               [-s] [--hash[=N]] [--abbrev[=N]] REF...
```

Behavioral details:

- no matching refs returns status 1 without inventing output;
- `--verify` requires exact fully-qualified ref names;
- `--verify --quiet` reports success/failure only through the exit status;
- `--head` includes `HEAD` even when a namespace filter is active;
- `--branches` and legacy `--heads` select `refs/heads/*`;
- `--branches --tags` includes both namespaces;
- `--dereference` emits annotated tag targets as `refs/tags/name^{}`;
- `-s` prints full object IDs only;
- bare `--hash` prints full object IDs only, while `--hash=N` uses exactly N
  recorded hex digits;
- `--abbrev` defaults to a minimum of 12 digits, and explicit values below 4
  are clamped to the project's 4-digit minimum before uniqueness expansion.

## Integrity and scope boundary

Enumeration reads `packed-refs` strictly before producing records. Invalid
object-ID syntax, unsafe packed ref names, malformed loose direct refs, or
symbolic-ref failures are surfaced rather than silently normalized. Tag peeling
uses the object database only when the caller asks for dereferencing.

This phase intentionally does **not** implement `show-ref --exists` or the
stdin-transforming `show-ref --exclude-existing[=<pattern>]` mode. Those have
different existence/stream-processing semantics and should be added only with
their own focused tests rather than approximated through normal enumeration.

## Regression coverage

`tests/test_phase79.py` covers packed-only refs, loose-over-packed shadowing,
HEAD and namespace filters, tail patterns, exact verification, annotated and
lightweight tags, broken tag targets, malformed ref storage, read-only behavior,
formatting modes, installed CLI routing, quiet verification, and no-match exit
status.
