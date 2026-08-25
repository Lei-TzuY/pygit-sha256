# Phase 85 — `for-each-ref --points-at` object filtering

Phase 85 extends structured ref queries with Git-style object-target filtering while reusing the existing loose/packed ref backend and shared SHA-256 object-ish resolver.

## CLI

```bash
pygit for-each-ref --points-at=HEAD
pygit for-each-ref --points-at=HEAD --format='%(refname:short)'
pygit for-each-ref --points-at=main:file.txt refs/tags/
pygit for-each-ref --points-at=v1 --points-at=v2 --sort=refname
```

`--points-at=<object>` may be supplied repeatedly. Multiple targets use OR semantics: a ref survives when it points at any requested object.

## Matching semantics

A ref matches when either:

- its stored object ID equals a requested object; or
- it is an annotated tag whose recursively peeled target equals a requested object.

This distinction matters for annotated tags: filtering by the tag object's own ID selects the tag ref directly, while filtering by its target commit/blob also selects that same tag through peeling. Lightweight refs simply compare their stored object ID.

Targets are arbitrary object-ish expressions resolved by the shared modern revision layer, so commit-ish names, full or abbreviated SHA-256 IDs, annotated tags, and `REV:path` tree paths can be used. The filter is not commit-only; refs that point to blobs or trees are valid matches.

Selection order is deterministic: namespace/pattern selection happens first, then `--points-at`, then existing graph predicates such as `--contains`/`--merged`, followed by sorting and finally `--count`.

## Storage behavior

The implementation operates on the existing `RefRecord.oid` and `RefRecord.peeled_oid` fields. It therefore works transparently with loose refs, packed-only refs, loose objects, and packed-only objects. Corrupt referenced objects still fail through the existing strict object/ref readers instead of being silently treated as non-matches.

The command remains read-only and does not change refs, objects, the index, reflogs, or worktree files.

## Python API

```python
from pygit.ref_query import query_refs

records = query_refs(
    repo,
    points_at=["HEAD", "main:file.txt"],
    patterns=["refs/tags/"],
    sort_keys=["refname"],
)
```

## Regression coverage

`tests/test_phase85.py` covers direct refs, lightweight and annotated tags, raw tag-object matching, repeated-target OR semantics, non-commit `REV:path` targets, filter/sort/count composition, packed refs and packed objects, installed CLI routing/formatting, missing-target errors, and help output.
