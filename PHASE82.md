# Phase 82 — interactive `cat-file --batch-command`

Phase 82 extends the existing Phase 55 batch object inspector with Git's command-oriented streaming protocol.

## Added behavior

- `pygit cat-file --batch-command` reads one command per input line.
- `info <object>` emits the default batch metadata header:
  `<oid> <type> <size>`.
- `contents <object>` emits the same header, followed by the raw serialized object bytes and a trailing newline.
- Missing or malformed object expressions are record-local and emit `<object> missing` without aborting later commands.
- `flush` is accepted only with `--buffer`.
- `--buffer` groups output until `flush`; pending output is also published at clean end-of-input, matching process-exit flushing.
- If buffered command parsing fails before a `flush`, pending output is not leaked.
- Command parsing uses the first ASCII space as the protocol delimiter. Additional spaces belong to the object expression instead of being collapsed by generic whitespace splitting.
- Existing `--batch`, `--batch-check`, `-t`, `-s`, `-p`, and `-e` behavior is preserved through the new stable top-level adapter.
- Non-buffered batch responses are explicitly flushed after each record for interactive consumers.

## Python API

Phase 82 exports:

- `CatFileBatchCommand`
- `parse_batch_command(raw)`
- `format_batch_object(repo, expression, contents=False)`
- `run_batch_commands(repo, commands, buffered=False)`

`run_batch_commands()` yields byte chunks at the same boundaries where the CLI publishes output: once per command without buffering, or at `flush` / clean EOF with buffering.

## Verification

`tests/test_phase82.py` covers:

- command parsing and first-space preservation;
- binary object contents, including NUL bytes;
- metadata/content/missing responses;
- unbuffered response boundaries;
- buffered flush grouping and clean-EOF behavior;
- invalid `flush` usage;
- suppression of pending buffered output on parse failure;
- installed `python -m pygit cat-file --batch-command` routing;
- regression coverage for the pre-existing batch and single-object modes.

## Scope boundary

This phase intentionally keeps the existing default batch output format. Custom `--batch-command=<format>` atoms, `--batch-all-objects`, `--unordered`, `--follow-symlinks`, filters/textconv, mailmap toggling, and `-Z` framing remain separate concerns rather than being partially approximated here.
