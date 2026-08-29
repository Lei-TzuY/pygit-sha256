# Phase253 — rev-list filter omitted-object reporting

Phase253 adds the first Git-compatible line-oriented `rev-list --filter-print-omitted` path on top of the metadata-only object-filter stack.

## Supported surface

```text
pygit rev-list --objects --filter=blob:none --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --filter=object:type=commit|tree|blob --filter-print-omitted --missing=allow-promisor HEAD
```

The same line-oriented filter path may continue to use the existing `--missing=print` or `--missing=print-info` policies when every object omitted by the filter already has a genuine local SHA-256 identity.

Git documents `--filter-print-omitted` as useful only with `--filter=` and specifies that omitted object IDs are printed with a leading `~` character. Phase253 follows that framing for materialized local objects.

## SHA-256-native identity boundary

A local omitted object is emitted as:

```text
~<64-hex-local-sha256>
```

An unresolved foreign promise is different: until materialization pygit knows only its upstream/native transport SHA-1 and cannot derive the repository-visible SHA-256. Phase253 therefore refuses a `--filter-print-omitted` request if such an unresolved promise itself would need to appear on the `~` channel. It never pads, translates, or substitutes the native SHA-1 as a fake SHA-256.

This is intentionally stricter than blindly reproducing a foreign OID. Existing explicit `?` / `missing=yes` channels remain the only places where unresolved native identities may be exposed.

## Traversal design

The adapter reuses the Phase246–252 metadata-only filter and inventory machinery:

- filtered normal output is produced by the existing filter adapter;
- omitted objects are derived from the same selected inventory;
- `blob:none` reports local blobs that the filter removed;
- `object:type=...` reports local objects of non-matching types;
- the normal provided-object exemption is respected;
- `--filter-provided-objects` removes that exemption before omitted-object classification.

No object is intentionally fetched or materialized in order to decide whether it is omitted.

## Deliberate deferrals

Phase253 rejects `-z`, `--count`, `--boundary`, and `--objects-edge` together with `--filter-print-omitted`. Their mixed framing/order/count semantics are left for dedicated phases rather than being inferred from line-oriented output.

## Verification

Focused regression coverage checks:

- `blob:none` omitted records are genuine local SHA-256 blobs;
- `object:type=tree --filter-provided-objects` reports omitted commits and blobs while retaining trees;
- the option requires an active `--filter`;
- unmodelled NUL/count/boundary/object-edge combinations remain explicit errors.

The full repository test suite is run by GitHub Actions on Python 3.9 and 3.13 before this phase is treated as a green stacked baseline.
