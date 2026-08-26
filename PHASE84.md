# Phase 84 — custom `cat-file` batch formats

Phase 84 extends the Phase 55/82 streaming object inspector with Git-style custom response headers while preserving the existing SHA-256 object resolver and binary-content behavior.

## Added behavior

- `pygit cat-file --batch=<format>` customizes the header before each raw object payload.
- `pygit cat-file --batch-check=<format>` customizes metadata-only records.
- `pygit cat-file --batch-command=<format>` applies the same successful-object header formatting to `info` and `contents` commands.
- Optional formats are accepted only in the attached `=...` form. A space-separated value remains an invalid positional argument, matching Git's command-line grammar.
- Supported atoms:
  - `%(objectname)` — full 64-hex SHA-256 object ID
  - `%(objecttype)` — `blob`, `tree`, `commit`, or `tag`
  - `%(objectsize)` — serialized payload size in bytes
  - `%(rest)` — auxiliary stdin text for ordinary batch modes
- `%%` produces a literal percent sign. Other percent sequences remain literal.
- Unknown and unterminated atoms fail before stdin is consumed, so malformed formats cannot emit partial streaming output.

## `%(rest)` semantics

For `--batch` and `--batch-check`, requesting `%(rest)` changes input parsing: the first whitespace run terminates the object expression, that separator is removed, and the remaining text is preserved exactly. Without `%(rest)`, the entire newline-stripped line is still the object expression, preserving revision paths containing spaces.

For `--batch-command`, the command protocol remains authoritative: everything after the first ASCII space following `info` or `contents` is the object expression. No secondary rest split is performed and `%(rest)` expands to the empty string.

Missing object expressions always emit the canonical `<object> missing` form and do not apply the custom success format. This keeps failure detection stable across formats.

## API

Phase 84 exports:

- `format_batch_record(record, format_string, rest="")`
- `batch_format_uses_rest(format_string)`
- `split_batch_input(raw, format_string=None)`

The existing `format_batch_object()` and `run_batch_commands()` functions now accept `format_string=` as well.

## Verification

`tests/test_phase84.py` covers:

- all supported atoms, literals, `%%`, and literal non-atom percent sequences;
- invalid/unterminated format rejection before lookup or stdin output;
- whitespace and CRLF handling for `%(rest)`;
- revision paths containing spaces when `%(rest)` is absent;
- canonical missing records under custom formats;
- binary `--batch` contents after custom headers;
- empty formats;
- command-mode `%(rest)` semantics;
- custom formats with `--buffer` and `flush`;
- attached-only optional format syntax;
- unchanged default batch formatting.

## Scope boundary

Later phases now implement `--batch-all-objects`, `--unordered`, `--follow-symlinks`, `%(objectsize:disk)`, and NUL-framed `-Z` input/output. `%(deltabase)`, textconv/filters, and mailmap behavior still require separate storage or conversion semantics and remain focused follow-up work.
