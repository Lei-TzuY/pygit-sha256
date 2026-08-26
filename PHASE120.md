# Phase 120 — byte-faithful `cat-file -p`

Phase 120 extends Phase 119's exact stored-object reads to the single-object `cat-file -p` / `--pretty` path.

## Problem

Phase 119 made batch inspection byte-faithful, but the single-object pretty path still parsed commits into `CommitObject` and called `pretty_print()`. The commit model intentionally ignores headers it does not understand, so valid stored bytes such as `gpgsig` blocks or future extension headers could disappear from `cat-file -p` output even though batch mode returned them correctly.

That made two interfaces for the same object disagree and meant inspecting an imported signed commit could silently produce reconstructed content instead of the stored payload.

## Behavior

Single-object `cat-file -p` now starts from `inspect_object()`, which already exposes the exact validated payload introduced in Phase 119.

- commit, tag, and blob objects are written directly from the stored payload bytes;
- binary blob contents are never decoded or normalized when binary stdout is available;
- packed-only objects remain observational reads and are not materialized as loose objects;
- tree objects keep their existing human-readable entry listing rather than emitting the binary tree payload.

This matches Git's important distinction: trees require pretty formatting, while commit/tag/blob payloads are already the display content.

## Compatibility and safety

No storage format, object identity, pack layout, batch protocol, or revision-expression behavior changes. `-t`, `-s`, `-e`, batch modes, custom formats, NUL framing, and symlink traversal are unchanged.

The Phase 119 note that single-object pretty behavior was unchanged is superseded by this phase; the exact-read primitive itself is unchanged.

## Regression coverage

`tests/test_phase120.py` covers:

- loose commits containing `gpgsig` and an unmodeled extension header;
- packed-only commits with no loose-object recreation;
- binary blob byte fidelity including NUL and non-UTF-8 bytes;
- unchanged human-readable tree pretty output.
