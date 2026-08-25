# Phase 90 — `for-each-ref` pattern selection

Phase 90 extends the structured ref-query plumbing with Git-style exclusion and stdin-fed inclusion patterns. The goal is script-friendly ref selection without weakening the object, graph, sorting, or packed-ref behavior added in earlier phases.

## CLI

```bash
pygit for-each-ref --exclude='refs/heads/release/*'
pygit for-each-ref --exclude='refs/tags/*' refs/heads/
printf 'refs/heads/\nrefs/tags/\n' | pygit for-each-ref --stdin
printf 'refs/heads/release/*\n' | pygit for-each-ref --stdin --exclude=refs/heads/release/old
```

`--exclude=PATTERN` may be repeated. A ref is retained only when it passes the normal inclusion patterns and matches none of the exclusion patterns. Both sides use the existing `for-each-ref` full-ref matcher: literal patterns match an exact ref or ref prefix, while `*`, `?`, and `[` enable shell-style matching. This is intentionally not `show-ref` tail matching, so `--exclude=main` does not remove `refs/heads/main`.

`--no-exclude` clears exclusions accumulated earlier on the command line. `--stdin` reads newline-delimited inclusion patterns; blank records are ignored and non-newline whitespace is preserved. Positional patterns and active `--stdin` mode are mutually exclusive.

## Filtering order and safety

Exclusions are applied before a selected ref is converted into a `RefRecord`. That matters for corrupt or intentionally missing object targets: an excluded ref is not forced through object metadata loading merely to be discarded later.

The resulting set then composes with the existing `--points-at`, `--contains`, `--no-contains`, `--merged`, `--no-merged`, sorting, formatting, and `--count` stages. Count limiting therefore sees the final selected ref set rather than consuming slots with refs that should have been excluded.

Loose and packed refs use the same selection path. The operation remains read-only.

## Python API

`query_refs()` now accepts repeated exclusion patterns:

```python
from pygit.ref_query import query_refs, read_ref_patterns

patterns = read_ref_patterns(["refs/heads/\n", "refs/tags/\n"])
records = query_refs(
    repo,
    patterns=patterns,
    exclude_patterns=["refs/heads/release/*"],
    sort_keys=["refname"],
)
```

`read_ref_patterns()` exposes the stdin record normalization used by the CLI: it removes only line terminators, skips empty records, and preserves all other whitespace.

## Regression coverage

`tests/test_phase90.py` covers literal-prefix and glob exclusions, full-ref rather than tail matching, include/exclude composition, filtering before sort/count, interaction with `--points-at`, excluded broken-object refs, packed refs, stdin blank-line handling, stdin globs, stdin plus exclusions, positional/stdin conflicts, `--no-exclude` reset behavior, and installed CLI help.
