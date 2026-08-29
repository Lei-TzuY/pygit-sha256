# Phase257: `rev-list -z --filter-print-omitted`

Phase257 composes the metadata-only object-filter omission channel with Git's NUL object-record mode without inventing a new protocol.

## Native Git behavior

Current Git sets `line_term` and `info_term` to NUL when `-z` is parsed. Normal object records therefore use the structured object protocol already implemented by pygit, including fields such as `path=...`, `boundary=yes`, and `missing=yes`.

The omitted-object loop is different. In current `builtin/rev-list.c`, `--filter-print-omitted` is emitted after filtered traversal using a hard-coded `printf("~%s\n", oid_to_hex(oid))`. It does not use `line_term` or `info_term`. Consequently, native Git deliberately exposes a mixed stream under `-z`:

1. surviving traversal/object records use NUL framing;
2. collected omitted objects use legacy newline-framed `~<oid>` records;
3. missing-object records, when requested, resume NUL framing.

Phase257 matches that observable behavior. In particular, it does **not** invent an undocumented `omitted=yes` NUL token.

## Implementation

The existing filter adapter remains authoritative for object selection. Phase257 captures its NUL projection, partitions records structurally at object-id fields, and separates records carrying `missing=yes` from ordinary traversal records. It then emits:

`NUL traversal -> newline ~omitted -> NUL missing`

For line-oriented operation, the Phase253-256 ordering remains unchanged:

`traversal / edges / boundaries -> ~omitted -> ?missing -> count`

The NUL parser treats each 40- or 64-hex object-id field as the beginning of a record. Metadata fields are always tokenized (`path=`, `type=`, `boundary=yes`, `missing=yes`), so even a hexadecimal-looking pathname cannot be mistaken for the next object id.

## SHA-256-native boundary

Every `~` omission remains a genuine local 64-hex SHA-256 identity. If `blob:none` would need to report an unresolved foreign promise whose content is not materialized, pygit continues to fail explicitly rather than padding, translating, or synthesizing a SHA-256 from the foreign native SHA-1.

NUL missing records remain the only structured path where an unresolved native identity may appear, and such a record carries `missing=yes` explicitly.

## Scope

Phase257 covers:

- `--objects -z --filter=blob:none --filter-print-omitted`;
- explicit `--missing=allow-promisor`, `print`, and `print-info` projections supported by the underlying NUL adapter;
- `object:type=commit|tree|blob` with the native empty omitted set;
- existing `--filter-provided-objects` semantics.

`-z --count` and `-z --objects-edge` remain rejected by the existing Git-compatibility guards and are not weakened by this phase.
