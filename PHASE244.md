# Phase 244 — promisor-aware `rev-list -z` metadata framing

Phase 244 adds the first machine-readable NUL-delimited object protocol for the
metadata-only partial-clone traversal:

```text
pygit rev-list --objects -z --missing=allow-promisor <revisions>
pygit rev-list --objects -z --missing=print <revisions>
pygit rev-list --objects -z --missing=print-info <revisions>
pygit rev-list --objects --boundary -z --missing=print-info <revisions>
```

## Git-compatible record framing

Current Git documents `-z` as a record protocol, not as a plain newline
replacement. Each object starts with an OID field and optional metadata follows
as independent NUL-terminated `token=value` fields:

```text
<oid> NUL
<oid> NUL path=<path> NUL
<oid> NUL boundary=yes NUL
<oid> NUL missing=yes NUL [path=<path> NUL] [type=<type> NUL]
```

Phase 244 follows that model for the inventory-backed missing-object path:

- ordinary present objects use their genuine local 64-hex SHA-256;
- boundary commits do not use the textual `-` prefix under `-z`; they carry
  `boundary=yes` metadata;
- unresolved promises do not use the textual `?` prefix under `-z`; they carry
  `missing=yes` metadata;
- `--missing=print` reports only `missing=yes` for an unresolved promise;
- `--missing=print-info` additionally reports containing `path=` and `type=`;
- paths are emitted verbatim without C-style quoting or newline truncation;
- `--missing=allow-promisor` continues to omit expected missing promises.

## SHA-256-native boundary

A foreign `blob:none` promise does not have a derivable local SHA-256 until its
contents arrive. Phase 244 therefore keeps the same dual-domain rule as the
previous missing-object phases:

- present/boundary records start with real local SHA-256 object ids;
- an unresolved foreign SHA-1 may start a record only when that same record is
  explicitly marked `missing=yes`;
- no SHA-1 is padded, translated, or exposed as a fake repository SHA-256.

The traversal remains metadata-only and does not call either the single-object
or batch promisor materialization seams.

## Deliberate scope

This phase intercepts `-z` only when an inventory-backed `--missing` mode is
present. General non-promisor `rev-list -z` remains a separate extension so the
implementation does not silently change ordinary missing-object/error behavior.

Git documents `-z` as compatible with `--objects`, `--boundary`, and
`--missing` output. Accordingly, Phase 244 rejects `--objects-edge` and
`--count` in this NUL protocol instead of inventing undocumented mixed framing.

## Tests

`tests/test_phase244.py` covers:

- exact NUL token framing for present and missing objects;
- 64-hex local SHA-256 versus 40-hex native missing identities;
- `boundary=yes` framing instead of `-<oid>`;
- plain `print` versus richer `print-info` metadata;
- raw newline-containing path preservation;
- `allow-promisor` omission;
- zero single/batch network fetching and unchanged promisor state;
- explicit rejection of `--objects-edge` and `--count` under `-z`.
