# Phase 140 — byte-faithful rev-list raw headers

Phase 140 adds Git-style `rev-list --header` output on top of the existing revision selector and Phase 139 timestamp presentation.

## Commands

```bash
pygit rev-list --header HEAD
pygit rev-list --header --timestamp HEAD
pygit rev-list --header --parents release..HEAD
pygit rev-list --header --boundary release..HEAD
```

Each commit record starts with the normal rev-list commit line. It is followed by the commit object's raw stored headers and message, and the record is terminated by a NUL byte. Message lines are indented by four spaces, including blank message lines, matching native Git's raw-header presentation.

## Byte fidelity

The implementation uses `ObjectStore.read_store_bytes()` rather than rebuilding a commit through `CommitObject`. This preserves stored headers byte-for-byte, including multiline `gpgsig` values and unknown extension headers, and works for loose, packed-only, and alternate-backed objects without materializing a loose copy.

Only the display transformation required by Git is applied: the object envelope (`commit <size>\0`) is removed and every logical commit-message line is prefixed with four spaces. The original header bytes and message bytes are otherwise retained.

## Composition

`--timestamp`, side/boundary markers, `--parents`, and `--children` remain part of the leading rev-list record line. Boundary commits receive raw header bodies too. `--count` suppresses commit records as before and therefore remains a plain textual count without NUL records.

Object streams retain non-commit object lines as ordinary newline-terminated records; only commit records receive raw bodies and NUL separators.

## Safety

The mode is read-only. It does not write loose objects, modify packs, refs, reflogs, the index, or the worktree. Packed-only commits remain packed-only after inspection.

Pretty-format families, mailmap/date formatting, path-limited history simplification, reflog walks, and missing/promisor-object modes remain separate future work.
