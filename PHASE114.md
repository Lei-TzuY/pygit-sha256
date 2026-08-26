# Phase 114 — pack-refs include/exclude patterns

Phase 114 extends the existing packed-reference backend with Git-style pattern selection for `pack-refs`.

## Commands

```bash
pygit pack-refs --include='refs/heads/release/*'
pygit pack-refs --include='refs/heads/*' --exclude='refs/heads/wip/*'
pygit pack-refs --all --exclude='refs/remotes/archive/*'
```

Both `--include` and `--exclude` may be repeated.

## Selection semantics

The selection order mirrors native Git behavior observed with Git 2.47.3:

1. without `--all` or `--include`, only loose tags are selected by default;
2. one or more `--include` patterns replace that default tag set with the union of matching loose direct refs;
3. `--all` selects every loose direct ref and is not narrowed by include patterns;
4. excludes are applied last and always win;
5. symbolic refs remain loose because only direct refs enter the candidate set.

Patterns are case-sensitive full-refname globs. `*` may match `/`, so a pattern such as `refs/heads/topic*` also matches `refs/heads/topic/deep/leaf`.

## Safety and compatibility

The Phase 54 publication model is unchanged. Existing packed records are preserved unless a selected loose ref replaces the same name. Excluded loose refs are not pruned and continue to shadow any older packed value, preventing packed-value resurrection.

Pattern filtering composes with `--no-prune`; selected refs can be written to `packed-refs` while their loose files remain present.

The packed-refs file format, peeled annotated-tag records, ref deletion semantics, and transparent packed-ref readers are unchanged.

`pack-refs --auto` is intentionally not implemented in this phase. Native automatic packing is backend- and repository-shape-dependent, so exposing it as a no-op would be misleading.

## Regression coverage

`tests/test_phase114.py` covers:

- include patterns replacing default tag selection;
- repeated include union semantics;
- exclude precedence;
- `--all` composition;
- nested-ref matching where `*` spans `/`;
- `--no-prune` composition;
- preservation of an excluded loose shadow over an older packed value;
- installed CLI and help behavior.
