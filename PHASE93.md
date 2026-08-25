# Phase 93 — `cat-file -Z` NUL framing

Phase 93 adds binary-safe NUL-delimited batch framing to the advanced `cat-file` plumbing.

## CLI

```bash
printf 'HEAD\0missing\0' | pygit cat-file --batch-check -Z
printf 'info HEAD\0flush\0' | pygit cat-file --batch-command --buffer -Z
pygit cat-file --batch-check --batch-all-objects -Z
```

`-Z` is accepted only with `--batch`, `--batch-check`, or `--batch-command`. It changes both batch input and batch output record terminators from LF to NUL, matching modern Git's scripting-safe protocol. `--batch-all-objects` has no stdin records to parse, but its output is still NUL-framed.

## Framing boundary

The implementation carries the active delimiter through the batch formatter and command runner. It does not rewrite payload bytes after formatting. This matters because:

- custom formats may themselves contain linefeeds;
- `%(rest)` data may contain linefeeds under NUL-framed input;
- raw object contents may contain arbitrary LF or NUL bytes.

Only protocol terminators change. For `--batch` and `contents` commands the shape becomes:

```text
<header> NUL <object bytes> NUL
```

while metadata-only records become:

```text
<header> NUL
```

Missing-object records likewise end in NUL. The object size remains authoritative for locating the end of binary contents.

## Streaming

The CLI reads NUL-framed stdin incrementally in fixed-size chunks rather than buffering the complete input. A final unterminated record is still processed at EOF, while a trailing delimiter does not synthesize an extra empty record. Embedded CR/LF bytes are preserved as data because only the active NUL delimiter is removed.

## Composition

NUL framing composes with the existing custom batch formats, `%(rest)`, `--buffer`, `--batch-command`, `flush`, `--batch-all-objects`, loose objects, and packed objects. Existing newline-framed APIs remain the default and keep their prior behavior.

## Regression coverage

`tests/test_phase93.py` covers API-level framing, embedded newlines in `%(rest)`, default and custom batch-check output, binary blob contents containing both LF and NUL bytes, buffered batch-command flushing, all-object enumeration, CLI grammar validation, and help output.
