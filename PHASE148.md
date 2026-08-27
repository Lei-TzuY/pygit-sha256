# Phase 148 — checkout-index temporary conflict extraction and stdin streaming

Phase 148 closes the compatibility boundary left by Phase 126: pygit can now export temporary index files, materialize all available unmerged stages in one invocation, and consume pathname streams from stdin.

## Motivation

The persistent multi-stage index already exposes stage 1 (base), stage 2 (ours), and stage 3 (theirs), and merge/cherry-pick/rebase conflicts populate those stages. External merge tooling still had to issue separate checkout commands and invent its own file-to-stage mapping.

Phase 148 adds the missing plumbing protocol:

```bash
pygit checkout-index --stage=all conflict.txt
```

`--stage=all` implies `--temp`.

It also adds streaming selection:

```bash
printf 'one.txt\ntwo.txt\n' | pygit checkout-index --stdin
printf 'conflict.txt\0' | pygit checkout-index --stage=all -z --stdin
```

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

## Stdin pathname protocol

`--stdin` replaces command-line pathname arguments. LF is the default input separator; `-z` switches stdin framing to NUL. Empty stdin is a successful no-op so producer pipelines can yield zero records safely.

`--stdin` is rejected with explicit path arguments or `--all`, preventing ambiguous double selection. When stdin is combined with temporary extraction, `-z` controls both input framing and output mapping framing.

## Temporary-file semantics

Generated files are unique regular files beneath the repository top level. Their basenames start with `.merge_file_` and contain neither directory separators nor whitespace, so the stdout mapping is safe for simple consumers.

Temporary mode never writes the tracked destination. `--prefix` is accepted but ignored because temporary names remain relative to the repository top level. `--force` is irrelevant because each temp path is uniquely allocated.

Symlink entries are written as ordinary files containing the stored link-target bytes rather than becoming filesystem symlinks. Submodule entries remain unsupported because pygit has no nested-repository materialization contract for `checkout-index`.

## Failure behavior

The implementation validates every selected index object before creating the first temporary file. This prevents a missing or corrupt later object from leaving an incomplete mapping set.

If file creation fails after output generation has begun internally, all temp files created by that API call are removed before the exception is propagated. The index, refs, worktree conflict file, and operation state remain untouched.

## Python API

`checkout_index_temp()` returns `CheckoutTempRecord` values. Each record stores the tracked pathname and its generated `(stage, Path)` pairs; `file_for(stage)` provides a convenient lookup.

```python
records = checkout_index_temp(repo, ["conflict.txt"], stage="all")
base = records[0].file_for(1)
ours = records[0].file_for(2)
theirs = records[0].file_for(3)
```

Numeric stages 0-3 are also supported with `checkout_index_temp(..., stage=N)` for compatibility with pygit's existing stage API.

## Regression coverage

`tests/test_phase148.py` covers:

- exact base/ours/theirs bytes from a real porcelain merge conflict;
- installed `--stage=all` automatically enabling temp mode;
- native three-field mapping format;
- stage-zero-only path omission;
- asymmetric conflict `.` placeholders;
- numeric `--temp --stage=N` mappings;
- ignored `--prefix` in temp mode;
- NUL mapping framing;
- symlink content written as a regular temp file;
- whole-selection object prevalidation before temp creation;
- LF stdin path selection;
- NUL stdin path selection;
- combined NUL stdin + temp mapping mode;
- option-conflict and empty-stdin behavior.

## Remaining checkout-index surface

This phase intentionally does not add skip-worktree bits, `--index`, `--no-create`, or quiet mode. Those options affect index stat refresh, sparse-checkout semantics, or overwrite policy and can be added independently without changing the Phase 148 mapping contract.
