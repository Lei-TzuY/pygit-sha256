# Phase 159 — staged status rename detection

Phase 159 restores the staged rename work that originally lived in the stale
Phase 152 branch, rebuilt directly on the current Phase 158 main line. The
rebuild deliberately preserves the intervening status features: stash headers,
porcelain-v1 pathname framing, untracked-file modes, and ignored-path modes.

## What changes

`pygit status` now detects HEAD-to-index delete/add pairs whose blob contents are
similar enough to represent a staged rename. Detection is presentation-only:
`Repository.status()` and the persistent index format remain unchanged.

Supported controls:

```bash
pygit status --renames
pygit status --no-renames
pygit status --find-renames
pygit status --find-renames=90%
```

`--find-renames` without an explicit value uses the normal 50% threshold.
Common Git similarity spellings are accepted:

- `5` -> 50%
- `05` -> 5%
- `90` -> 90%
- `0.75` -> 75%
- `90%` -> 90%

Git documents `--renames`, `--no-renames`, and `--find-renames[=<n>]` for
`git status`; porcelain v2 describes renamed/copied records using type `2` and
an `R<score>` or `C<score>` token.

## Detection model

The detector compares:

1. paths present in HEAD but absent from the stage-zero index; and
2. paths absent from HEAD but newly present in the stage-zero index.

Unmerged stage 1/2/3 paths are excluded. Candidate pairs are scored and selected
one-to-one in descending similarity order with deterministic pathname
 tie-breaking.

Exact object-id matches are always `R100`. For non-identical blobs pygit uses
`difflib.SequenceMatcher` over raw blob bytes. This gives a deterministic,
educational approximation of Git's diffcore rename scoring without claiming to
reimplement every diffcore heuristic.

An unstaged filesystem move is intentionally not synthesized into a rename. If
`old.txt` is merely moved to `new.txt` in the worktree, status continues to show
an unstaged deletion plus an untracked destination until the move is staged.

## Long and short status

An exact staged rename appears in long status as:

```text
Changes to be committed:
        renamed:        old.txt -> new.txt
```

Short and porcelain v1 output use:

```text
R  old.txt -> new.txt
```

If the destination is modified after staging, the worktree column is preserved:

```text
RM old.txt -> new.txt
```

With porcelain v1 `-z`, Git reverses the human pathname order and removes the
arrow syntax:

```text
R  new.txt\0old.txt\0
```

Phase 159 follows that rule, including raw unquoted pathnames under NUL framing.

## Porcelain v2 type-2 records

Phase 151 added type `1` ordinary entries and type `u` unmerged entries. Phase
159 adds type `2` renamed entries:

```text
2 <XY> N... <mH> <mI> <mW> <hH> <hI> R<score> <path>\t<origPath>
```

For an exact staged rename:

```text
2 R. N... 100644 100644 100644 <head-oid> <index-oid> R100 new.txt\told.txt
```

Pygit remains SHA-256-native, so `<hH>` and `<hI>` are 64 hexadecimal
characters. With `-z`, the target and original pathname are separated by NUL,
and the record itself is NUL terminated.

## Interaction with newer status features

The Phase 159 rebuild composes rename detection with the features merged after
the original stale branch:

- `--show-stash` / `--no-show-stash`;
- bare `-z` implying porcelain v1;
- Git-style pathname quoting without `-z`;
- `-u/--untracked-files=no|normal|all`;
- `--ignored=traditional|matching|no`.

`--no-renames` restores the ordinary staged delete/add presentation in long,
short, porcelain-v1, and porcelain-v2 output.

## Scope boundary

Phase 159 implements rename (`R`) detection only. Copy (`C`) detection remains a
follow-up because Git enables it through `status.renames=copies` / copy-specific
policy and source-candidate rules. `status.renameLimit`, full diffcore-compatible
scoring, directory renames, and submodule-specific rename scoring are also
separate concerns.

## Regression coverage

`tests/test_phase159.py` covers:

- similarity-threshold parsing;
- exact `R100` detection;
- long, short, and porcelain-v1 rendering;
- porcelain-v1 explicit and implied `-z` target/source ordering;
- porcelain-v2 type-2 metadata and NUL framing;
- staged rename plus later worktree modification (`RM`);
- `--no-renames` fallback in all formats;
- threshold-controlled non-identical rename detection;
- unstaged filesystem moves remaining delete + untracked;
- composition with untracked and ignored modes;
- quoted versus raw rename pathname behavior;
- CLI help for rename controls.
