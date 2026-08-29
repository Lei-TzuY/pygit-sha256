# Phase 237 — Promisor-aware `rev-list --missing=print-info`

Phase 237 extends the metadata-only object inventory from Phases 232–236 with a
safe missing-object presentation mode:

```text
pygit rev-list --objects --missing=print-info <revisions>
```

## Identity model

pygit stores repository objects under SHA-256, while the current smart-HTTP
interoperability/promisor boundary still uses native Git SHA-1 object names.
For a filtered foreign blob whose bytes have not arrived yet, the correct local
SHA-256 cannot be known.

Phase 237 therefore keeps two output channels deliberately distinct:

- ordinary object lines contain repository-visible 64-hex SHA-256 ids;
- lines prefixed with `?` describe unresolved promised objects and contain the
  known 40-hex native SHA-1 transport identity.

A `?` identity is not accepted or represented as a local pygit object id. No
surrogate SHA-256 is generated.

## `print-info` framing

Git documents `--missing=print-info` as printing a missing object as:

```text
?<oid> [<token>=<value>]...
```

with `path=` and `type=` information inferred from the containing object when
available. Phase 237 uses the same missing-line shape:

```text
?<native-sha1> path=<path> type=<kind>
```

Paths containing spaces, control bytes, quotes, or backslashes are C-style
quoted. The leading `?` is the explicit missing/native identity channel; only
unprefixed object ids are repository-visible SHA-256 identities.

## Metadata-only behavior

The implementation consumes `promisor_object_inventory()` directly. It does
not resolve `TreeEntry.sha`, materialize any promise, or mutate promisor state.
The existing revision-selection controls remain available:

- `--all`
- `--first-parent`
- `--topo-order`
- `--reverse`
- `--skip`
- `--max-count` / `-n`
- `--no-object-names`

`--no-object-names` affects ordinary present-object pathname decoration only;
`print-info` still includes `path=` because that field is the requested missing
object metadata.

## Deliberate limits

This phase intentionally rejects the following combinations with
`--missing=print-info` until their native framing/counting behavior is modeled
and regression-tested:

- `--objects-edge`
- `--boundary`
- `--count`

Plain `--missing=print` is also left for a later phase. This keeps Phase 237
focused on the richer mode that can explicitly expose the native hash-domain
context rather than introducing an ambiguous bare missing OID first.

The implementation remains stricter than Git's generic `print-info` in one
important respect: it reports expected promisor omissions known to the
metadata-only inventory. Arbitrary repository corruption is not converted into
an apparently valid missing-object record.

## Git compatibility reference

Current Git documentation describes `--missing=print-info` as `print` plus
additional containing-object information and defines the supported information
tokens as `path=` and `type=`. The Phase 237 presentation follows that framing
while documenting pygit's SHA-1 transport / SHA-256 repository split explicitly.

## Tests

`tests/test_phase237.py` covers:

- real foreign `blob:none` promises;
- zero single-object and batch network fetches;
- unchanged promisor state;
- 64-hex SHA-256 present-object lines;
- 40-hex native SHA-1 identities only on `?` lines;
- `path=` and `type=` metadata;
- quoting of paths containing spaces;
- `--no-object-names` interaction;
- ordinary-repository transparency;
- explicit rejection of unmodeled boundary/edge/count combinations.
