# Phase 152 — status staged rename detection

Phase 152 adds real HEAD-to-index rename detection to the Phase 150/151 status
stack.  It turns a staged delete/add pair into a single rename record only after
matching the source and target blob contents; the renderer no longer has to
invent rename state from path names alone.

## CLI

Rename detection is enabled for short/porcelain status by default and can be
controlled explicitly:

```bash
pygit status -s --renames
pygit status --porcelain=v1 --no-renames
pygit status --porcelain=v2 --find-renames
pygit status --porcelain=v2 --find-renames=90%
```

`--find-renames` without a value uses the normal 50% threshold.  Git-style
fraction spellings are accepted as well:

- `5` -> 50%
- `05` -> 5%
- `90` -> 90%
- `0.75` -> 75%

## Detection model

The detector compares paths deleted between HEAD and the stage-zero index with
paths added between HEAD and the index.  Conflict-stage paths are excluded.
Candidate pairs are scored and then selected one-to-one in descending similarity
order, with deterministic pathname tie-breaking.

Exact object-id matches are `R100`.  For non-identical blobs pygit uses a
byte-sequence `difflib.SequenceMatcher` score.  This preserves the user-facing
threshold contract while keeping the educational implementation compact; it is
not intended to reproduce every heuristic in Git's internal diffcore-rename
engine.

An unstaged filesystem move is deliberately *not* synthesized into a rename:
with the old path still present in the index, status continues to report a
worktree deletion plus an untracked destination until the move is staged.

## Porcelain v1 / short format

A staged exact rename now renders as:

```text
R  old.txt -> new.txt
```

If the destination is modified again after staging, the two-column state is
preserved:

```text
RM old.txt -> new.txt
```

With `-z`, porcelain v1 uses Git's machine-parsing pathname order:

```text
R  new.txt\0old.txt\0
```

The target comes first, followed by the original pathname.

## Porcelain v2 type-2 records

Phase 151 implemented ordinary `1` records and unmerged `u` records.  Phase 152
adds the third tracked-record shape used for renames:

```text
2 <XY> N... <mH> <mI> <mW> <hH> <hI> R<score> <path>\t<origPath>
```

For an exact staged rename this is typically:

```text
2 R. N... 100644 100644 100644 <head-oid> <index-oid> R100 new.txt\told.txt
```

Because pygit is SHA-256-native, `<hH>` and `<hI>` remain 64 hexadecimal
characters.  Under `-z`, the target and original pathname are separated by NUL
and the complete record is NUL-terminated.

## Compatibility boundaries

This phase implements rename (`R`) detection.  Copy (`C`) detection remains a
separate step because native Git only enables status copy detection under the
`status.renames=copies` policy and applies additional source-candidate rules.
Submodule rename scoring and a full diffcore-compatible similarity engine are
also intentionally separate concerns.

## Regression coverage

`tests/test_phase152.py` covers:

- Git-style similarity-threshold parsing;
- exact `R100` detection;
- short and porcelain-v1 arrow rendering;
- porcelain-v1 NUL target/source ordering;
- porcelain-v2 type-2 metadata and NUL framing;
- staged rename plus later worktree modification (`RM`);
- `--no-renames` restoring ordinary delete/add records;
- threshold-controlled non-identical rename detection;
- unstaged filesystem moves remaining delete + untracked;
- installed help for rename controls.
