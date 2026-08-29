# Phase 252 — `rev-list --filter-provided-objects`

Phase252 completes the provided-object side of the metadata-only `rev-list`
object-filter contract.

## Scope

Supported combinations now include:

```text
pygit rev-list --objects --filter=object:type=commit --filter-provided-objects ...
pygit rev-list --objects --filter=object:type=tree --filter-provided-objects ...
pygit rev-list --objects --filter=object:type=blob --filter-provided-objects ...
pygit rev-list --objects -z --filter=object:type=... --filter-provided-objects ...
```

The option also composes with the already-supported `blob:none` filter. In the
current commit-rooted traversal model, provided roots are commits, so filtering
them with `blob:none` does not change which root commits survive; the important
observable change is for `object:type=tree|blob`, where a provided commit root
must no longer bypass the requested type filter.

## Git compatibility

Git documents object filters as exempting explicitly provided objects by
default. `--filter-provided-objects` removes that exemption and applies the
active filter to those objects as well.

A native SHA-256 Git 2.47.3 comparison over a deterministic three-commit history
confirmed the expected behavior for `object:type=tree`:

- without `--filter-provided-objects`, the provided `HEAD` commit is emitted in
  addition to the three reachable tree objects;
- with `--filter-provided-objects`, `HEAD` is filtered out and only the three
  trees remain;
- the corresponding `--count` result changes from `4` to `3`.

Phase252 models this by making the provided-root exemption set empty before
line, count, or NUL rendering. Explicit `--objects-edge` records remain a
separate presentation channel and are not reclassified as provided roots.

## SHA-256-native / partial-clone boundary

No object identity rules change in this phase. Present commits, trees, blobs,
boundaries, and object edges continue to use genuine repository-visible
64-hex SHA-256 object IDs. Foreign 40-hex SHA-1 identities remain confined to
explicit missing/promisor metadata channels. The new option does not introduce
materialization and does not synthesize surrogate SHA-256 values.

## Validation

Focused regression tests cover:

- filtering the provided `HEAD` root from NUL tree output;
- filtered object counts;
- applying the rule to every `--all` ref tip;
- retaining provided roots when their type actually matches;
- composition with `blob:none`;
- rejecting `--filter-provided-objects` without an active `--filter`.

The complete Python 3.9 / 3.13 test matrix is run by GitHub Actions on the PR
head before this phase is considered green.
