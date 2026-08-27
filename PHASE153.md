# Phase 153 — status porcelain-v1 pathname framing

Phase 153 fixes a machine-readable status compatibility gap left after the porcelain-v2 work in Phases 151–152. `pygit status -z` now follows Git by implying porcelain v1 when no explicit porcelain version is supplied, and short/porcelain-v1 pathnames use stable C-style quoting whenever LF framing would otherwise be ambiguous.

## Commands

```bash
pygit status -z
pygit status --porcelain=v1
pygit status --porcelain=v1 -z
pygit status --short
pygit status -z --branch
pygit status --porcelain=v2 -z
```

## `-z` implication

Git defines `-z` as NUL termination and, when no other status format is selected, an implicit `--porcelain=v1`. Pygit previously rejected this form with an argument error. Phase 153 removes that incompatibility: `pygit status -z` is now exactly the v1 NUL protocol rather than an invalid invocation.

An explicit `--porcelain=v2 -z` remains porcelain v2; the implication only fills in a missing format.

## Pathname safety

LF-framed short and porcelain-v1 records now C-quote pathnames containing control characters, double quotes, or backslashes. Plain printable ASCII names remain unchanged. This prevents a filename containing a newline from manufacturing extra apparent status records.

With `-z`, pathnames are intentionally emitted raw. NUL framing is unambiguous, so newlines, quotes, and backslashes are preserved byte-for-character instead of being escaped. Branch headers participate in the same NUL-framed stream.

## Compatibility and safety

The change is presentation-only and read-only. It does not alter status classification, index contents, refs, reflogs, objects, worktree files, porcelain-v2 metadata, or stash reporting. Rename-specific v1 framing remains future work because pygit's status engine does not yet perform rename detection; Phase 153 does not invent partial rename records without a real detector.
