# Phase 139 — checkout-index temporary conflict extraction

Phase 139 closes the compatibility boundary left by Phase 126: pygit can now export temporary index files and can materialize all available unmerged stages in one `checkout-index` invocation.

## Motivation

The persistent multi-stage index can already expose stage 1 (base), stage 2 (ours), and stage 3 (theirs), and high-level merge/cherry-pick/rebase conflicts now populate those stages. External merge tooling still had to issue three separate checkout commands and invent its own file-to-stage mapping.

Phase 139 adds the missing plumbing protocol:

```bash
pygit checkout-index --stage=all conflict.txt
```

`--stage=all` implies `--temp`, matching Git.

## Mapping protocol

For one numeric stage with `--temp`:

```text
TEMP<TAB>PATH<RS>
```

For `--stage=all`:

```text
STAGE1TEMP SP STAGE2TEMP SP STAGE3TEMP<TAB>PATH<RS>
```

Missing stages are represented by `.`. Stage-zero-only paths are omitted from `--stage=all`, including when explicitly named. A path absent from the index entirely remains an error.

`<RS>` is newline by default and NUL with `-z`.

## Temporary-file semantics

Generated files are unique regular files beneath the repository top level. Their basenames start with `.merge_file_` and contain neither directory separators nor whitespace, so the stdout mapping is safe for simple consumers.

Temporary mode never writes the tracked destination. `--prefix` is accepted but ignored, as native Git keeps temporary files relative to the top-level directory. `--force` is irrelevant because each temp path is uniquely allocated.

Symlink entries are written as ordinary files containing the stored link-target bytes rather than becoming filesystem symlinks. Submodule entries remain unsupported because pygit has no nested-repository materialization contract for `checkout-index`.

## Failure behavior

The implementation validates every selected index object before creating the first temporary file. This prevents a missing/corrupt later object from leaving an incomplete mapping set.

If file creation fails after output generation has begun internally, all temp files created by that API call are removed before the exception is propagated. The index, refs, worktree conflict file, and operation state remain untouched.

## Python API

`checkout_index_temp()` returns `CheckoutTempRecord` values. Each record stores the tracked pathname and its generated `(stage, Path)` pairs; `file_for(stage)` provides a convenient lookup.

```python
records = checkout_index_temp(repo, ["conflict.txt"], stage="all")
base = records[0].file_for(1)
ours = records[0].file_for(2)
theirs = records[0].file_for(3)
```

Numeric stages 0-3 are also supported with `checkout_index_temp(..., stage=N)`.

## Regression coverage

`tests/test_phase139.py` covers:

- exact base/ours/theirs bytes from a real porcelain merge conflict;
- installed `--stage=all` automatically enabling temp mode;
- native three-field mapping format;
- stage-zero-only path omission;
- asymmetric conflict `.` placeholders;
- numeric `--temp --stage=N` mappings;
- native-style ignored `--prefix` in temp mode;
- NUL record framing with `-z`;
- symlink content written as a regular temp file;
- whole-selection object prevalidation before temp creation.

## Remaining checkout-index surface

This phase intentionally does not add `--stdin`, skip-worktree bits, `--index`, `--no-create`, or quiet mode. Those options affect input framing, index stat refresh, sparse-checkout semantics, or overwrite policy and can be added independently without changing the Phase 139 mapping contract.
