# Phase 155 — status ignored modes

Phase 155 extends the Phase 154 status path-presentation layer with Git-style `--ignored[=<mode>]` handling. The repository status API remains an individual-path scanner; grouping and matching semantics stay in the CLI presentation layer.

## Commands

```bash
pygit status --ignored
pygit status --ignored=traditional
pygit status --ignored=matching
pygit status --ignored=no
pygit status --porcelain=v2 --ignored=matching
pygit status --porcelain=v1 --ignored=matching -z
```

A bare `--ignored` is equivalent to `--ignored=traditional`.

## Modes

`traditional` preserves the behaviour implemented in Phase 154. Ignored paths are collapsed to directory records unless `--untracked-files=all` is active, in which case individual ignored files are shown.

`no` suppresses ignored records while leaving ordinary tracked and untracked status unchanged.

`matching` reports paths that directly match the final ignore rule. If a directory itself matches an ignore pattern, that directory is emitted once and its ignored descendants are suppressed. If the directory does not itself match but its contents are individually ignored, the directory is not synthesized and the matching contents are emitted instead. Unlike `traditional`, matching-mode ignored records do not change merely because `--untracked-files=all` was selected.

## Ignore matcher support

`IgnoreMatcher.is_explicitly_ignored()` distinguishes a direct pattern match from ignored state inherited through a directory-only pattern. Later negated patterns retain precedence. Existing `is_ignored()` behaviour is unchanged for repository scans and existing Python callers.

## Output integration

The selected ignored-mode paths flow through long status, short/porcelain v1, porcelain v2, and NUL-framed `-z` output. Porcelain v1 continues to use `!!`; porcelain v2 continues to use `!` records.

## Compatibility and safety

All status operations remain read-only. Phase 155 does not change index contents, refs, objects, ignore files, or the low-level `Repository.status()` return contract. The implementation covers the ignore syntax already supported by pygit's matcher; full native Git ignore-pattern parity remains a separate concern.
