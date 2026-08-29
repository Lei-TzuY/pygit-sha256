# Phase 241 — Promisor-aware `rev-list --missing=print`

Phase 241 adds the plain missing-object presentation mode on top of the
metadata-only promisor traversal completed in Phases 232–240:

```text
pygit rev-list --objects [--boundary] --missing=print <revisions>
```

## Why this is a presentation adapter

Git documents `--missing=print` as continuing object traversal while emitting
missing object IDs with a leading `?`.  `--missing=print-info` is the richer
variant that emits the same missing ID plus containing-object metadata.

pygit already has the harder part: the Phase237–240 `print-info` path owns
revision selection, boundary snapshot roots, skip/max-count handling, counting,
promisor-state preservation, and zero-fetch traversal.  Phase241 therefore does
not duplicate that logic.  It translates the request to `print-info`, captures
that presentation, and removes only the `path=` / `type=` suffix from `?`
records.  Present-object and numeric count records pass through unchanged.

This keeps `print` and `print-info` behavior synchronized as later traversal
semantics evolve.

## SHA-256-native identity rule

Repository-visible present objects remain genuine 64-hex SHA-256 object IDs.
An unresolved foreign promise cannot have a local SHA-256 until its bytes are
available, so the explicit `?` missing-object channel carries the known native
transport SHA-1 identity:

```text
?<native-sha1>
```

The native SHA-1 is never accepted as a local pygit object ID and is never
padded, translated, or replaced with a surrogate SHA-256.

## Supported framing

Phase241 inherits the already-tested metadata-only framing from `print-info`,
including:

- `--all`
- `--first-parent`
- `--topo-order`
- `--reverse`
- `--skip`
- `--max-count` / `-n`
- `--no-object-names`
- `--boundary`
- `--count`

Under `--count`, missing `?` records are still emitted and the final integer
counts present objects only.  Missing promises are not included in that count.

`--objects-edge` remains deliberately deferred because the richer
`print-info` traversal does not yet model that combination either.

## Compatibility and safety

Git's documentation states that `--missing=print` is like permissive missing
traversal plus a list of missing objects, with each missing object ID prefixed by
`?`.  pygit's inventory path remains intentionally stricter about corruption:
it reports known promisor omissions rather than turning arbitrary repository
corruption into apparently valid missing-object output.

No promised object is materialized, no single-object or batch fetch is issued,
and promisor metadata is left unchanged.

## Tests

`tests/test_phase241.py` covers:

- plain `?oid` framing without `path=` / `type=` metadata;
- repository SHA-256 vs native missing SHA-1 domain separation;
- zero single-object and batch fetching;
- unchanged promisor state;
- `--count` present-only counting;
- boundary/count snapshot semantics;
- ordinary repository behavior;
- explicit deferral of `--objects-edge`.
