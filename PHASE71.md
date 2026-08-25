# Phase 71: safe unreachable-object pruning

Phase 71 adds `pygit prune` for conservative cleanup of expired **loose** objects that are no longer reachable from any recovery root.

## CLI

```bash
pygit prune
pygit prune --dry-run --verbose
pygit prune --expire=now
pygit prune --expire=30.days.ago
pygit prune --expire=never
pygit prune --expire=now <extra-head>...
```

The default cutoff is two weeks ago. Fresh unreachable objects are intentionally retained so an accidental ref movement does not immediately destroy recovery data.

Supported expiry forms are `default`, `now`, `never`, a Unix epoch timestamp, and `N.minutes.ago`, `N.hours.ago`, `N.days.ago`, or `N.weeks.ago`.

## Retention roots

Before considering any deletion, pruning preserves the full object closure reachable from:

- current `HEAD`, loose refs, and packed refs;
- every object currently present in the index;
- `.pygit/shallow` boundary commits;
- every non-zero old/new OID recorded anywhere under `.pygit/logs`;
- any extra revisions supplied on the command line.

A connectivity-only `fsck` runs first. A malformed current ref, missing reachable object, bad index root, broken shallow boundary, or object/type connectivity failure aborts the entire prune pass before any unlink.

Reflogs are parsed strictly rather than silently ignoring malformed records. If historical recovery metadata cannot be trusted, pruning fails closed.

## Deletion boundary

`prune` only considers canonical `objects/aa/<62hex>` loose paths. Packed objects are never removed.

For an unreachable loose object to be deleted it must:

1. be older than or equal to the configured expiry cutoff;
2. contain one complete zlib stream with no trailing or unconsumed data;
3. hash exactly to the SHA-256 encoded by its loose-object path; and
4. contain a parseable pygit object envelope.

All eligible candidates are validated before the first unlink. Malformed loose copies are retained and reported as warnings.

`--dry-run` performs the same discovery and validation but makes no filesystem changes. `--verbose` prints eligible object IDs.

## Python API

```python
from pygit import prune, PruneResult

result = prune(repo, dry_run=True)
print(result.oids)
print(result.kept_recent)
```

## Scope

This command intentionally does not remove objects from pack files. `prune-packed` removes verified duplicate loose copies of packed objects; pack compaction/rewrite remains the responsibility of pack maintenance such as `repack`.
