# Phase 160 — status staged copy detection

Phase 160 extends Phase 159's HEAD-to-index similarity layer from rename-only
classification to Git-style staged copy reporting.

The feature is deliberately policy driven. Basic status continues to detect
renames by default; copies are enabled only when repository configuration asks
for them:

```bash
pygit config status.renames copies
pygit status -s
```

`copy` is accepted as an alias for `copies`, matching Git.

## Configuration precedence

Status resolves similarity policy in the same order as Git:

1. `status.renames`
2. `diff.renames`
3. default basic rename detection

Recognized values are:

- `false`, `no`, `off`, `0` → no rename/copy detection;
- `true`, `yes`, `on`, `1` → rename detection only;
- `copies`, `copy` → rename plus copy detection.

The existing CLI switches override configuration:

- `--renames` selects rename-only mode, even if config says `copies`;
- `--no-renames` disables both rename and copy detection;
- `--find-renames[=<n>]` enables similarity detection and controls the common
  threshold. If it is the only CLI override, a configured `copies` policy is
  preserved. If combined with either explicit rename switch, the effective mode
  is rename-only.

This reproduces the native behavior probed for Phase 160, including the slightly
non-obvious `--no-renames --find-renames=<n>` combination: `--find-renames`
re-enables basic rename matching but does not re-enable configured copies after
an explicit rename switch.

## Normal copy-source eligibility

Git's ordinary copy detection does **not** search every tracked file as a source.
For performance reasons, a source is normally eligible only when the source file
itself changed in the same changeset.

Phase 160 mirrors that rule on the HEAD-to-index side:

- the source path must exist in both HEAD and stage zero;
- its indexed object or mode must differ from HEAD;
- the target must be newly added in the index;
- unresolved stage 1/2/3 paths are excluded.

An unmodified tracked file copied to a newly added path therefore remains an
ordinary `A` record even with `status.renames=copies`.

Likewise, modifying the source only in the working tree is not enough. The
source change must itself be staged, because status similarity classification is
based on HEAD versus index.

Git's much more expensive `--find-copies-harder` behavior, which also considers
unmodified sources, remains a separate future phase.

## Similarity scoring

The target is compared against the **HEAD preimage of the source**, matching the
metadata carried by Git's type-2 status record. This matters when the source was
modified and the new target contains that modified content: the copy score need
not be `C100`, because the similarity is measured from the old source content.

Exact object matches score 100. Non-identical pygit blobs use deterministic
`difflib.SequenceMatcher` byte similarity. This is an educational approximation
of diffcore scoring, not a claim of exact percentage parity with native Git.

The threshold is shared with Phase 159 rename detection and uses the existing
Git-style parser:

```bash
pygit status --find-renames=90%
pygit status --find-renames=90
pygit status --find-renames=5      # 50%
pygit status --find-renames=05     # 5%
pygit status --find-renames=0.75   # 75%
```

## Matching model

Rename pairing runs first. Any target already consumed by an `R` record is
excluded from copy detection.

For remaining added targets:

- every eligible changed source is scored;
- the highest-scoring qualifying source is selected;
- pathname order is used as a deterministic tie-break;
- one source may feed multiple copy targets;
- the source record itself remains visible, normally as staged `M`.

This differs from rename pairing, where source and target are consumed one-to-one.

## Human and porcelain-v1 output

With copy policy enabled, a changed source copied to a new path renders as:

```text
C  old.txt -> copy.txt
M  old.txt
```

Long status uses:

```text
copied: old.txt -> copy.txt
modified: old.txt
```

If the staged copy target changes again in the worktree, the two-column status is
preserved, for example `CM` or `CD`.

Under porcelain-v1 `-z`, type-2 path ordering follows Git's machine protocol:

```text
C  copy.txt\0old.txt\0
```

The target is first, then the source/original pathname.

## Porcelain v2

Phase 160 generalizes Phase 159's type-2 renderer. A copy record is:

```text
2 <XY> N... <mH> <mI> <mW> <hH> <hI> C<score> <path>\t<origPath>
```

A representative record is:

```text
2 C. N... 100644 100644 100644 <head-source-oid> <target-index-oid> C63 copy.txt\told.txt
```

Pygit remains SHA-256-native, so object IDs are 64 hexadecimal characters.
Under `-z`, target and source pathnames are NUL separated and the overall record
stream remains NUL framed.

The modified source still receives its own ordinary type-1 `1 M.` record.

## Compatibility boundary

Phase 160 implements normal status copy detection only. It intentionally does
not yet implement:

- `--find-copies-harder` / unmodified-source scanning;
- Git's full diffcore scoring algorithm;
- `status.renameLimit` / `diff.renameLimit` exhaustive-search cutoffs;
- submodule-specific copy semantics.

These are independent extensions and can be added without changing the
`Repository.status()` contract.

## Regression coverage

`tests/test_phase160.py` covers:

- `status.renames` and `diff.renames` precedence;
- `copy` / `copies` aliases and boolean values;
- unmodified source rejection;
- worktree-only source changes not qualifying;
- staged modified source copy matching;
- short, long, porcelain-v1, and porcelain-v2 output;
- porcelain-v1/v2 NUL path framing;
- `CM` destination worktree state;
- one source feeding multiple copy targets;
- explicit `--renames` / `--no-renames` overriding copy policy;
- `--find-renames` preserving configured copies when used alone;
- threshold-controlled copy classification;
- `--find-renames` re-enabling basic rename matching from disabled config;
- `diff.renames=copies` fallback behavior.
