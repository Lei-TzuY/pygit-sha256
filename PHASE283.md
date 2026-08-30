# Phase 283 — Plain blob-limit omitted NUL framing

Phase283 completes the remaining non-`--in-commit-order` structured-output composition for `git rev-list --filter=blob:limit=<n>[kmg]` by combining Phase281's omitted-object channel with Phase282's plain structured `-z` traversal.

## Scope

Supported combinations now include:

- `--objects -z --filter=blob:limit=<n>[kmg] --filter-print-omitted`
- `--no-object-names`
- `--missing=allow-promisor|print|print-info`
- `--count`
- trusted promisor-size metadata from the existing protocol-v2 `object-info` path

The implementation does not add a second object walker or a new NUL wire format. It captures the already-filtered Phase282 projection, partitions present and missing NUL records with the mature omission helpers, and inserts the genuine local SHA-256 omission records in Git-compatible order.

## Git-compatible framing

Current Git deliberately mixes delimiters for this command family:

1. present object records use the structured NUL protocol,
2. omitted objects remain newline-framed as `~<oid>`,
3. explicit missing-object diagnostics return to structured NUL records,
4. `--count` ends with a normal newline-terminated decimal integer.

Phase283 preserves exactly that ordering and does not invent an `omitted=yes` metadata token.

## SHA-256-native identity boundary

Every `~<oid>` emitted by Phase283 is a genuine local 64-hex SHA-256 object id.

An unresolved promised blob may be classified from trusted size metadata without fetching content. If it survives the size filter, its native 40-hex transport identity may remain in the existing explicit missing-object channel. If it is filtered, however, no local SHA-256 identity exists for the omission channel, so pygit fails before emitting any bytes rather than padding, translating, guessing, or synthesizing an object id.

Missing trusted size metadata remains a hard pre-render error. Classification never materializes content merely to decide membership or obtain an omission identity.

## Regression coverage

`tests/test_phase283.py` covers:

- exact-threshold local blob omission,
- mixed NUL/newline framing,
- structured `path=` preservation for surviving blobs,
- omission-before-count ordering,
- a native SHA-256 Git framing probe.

The Phase282 deferral regression is retired and replaced with an assertion that the follow-up omission adapter now owns the composition.
