# Phase 139 — rev-list raw commit timestamps

Phase 139 adds Git-style `rev-list --timestamp` presentation. Each emitted commit record is prefixed with the commit's raw committer timestamp while the underlying revision selection remains unchanged.

## Commands

```bash
pygit rev-list --timestamp HEAD
pygit rev-list --timestamp --parents HEAD
pygit rev-list --timestamp --children HEAD
pygit rev-list --timestamp --left-right --boundary A...B
pygit rev-list --timestamp --objects HEAD
```

Git places the timestamp before the normal commit record. Side and boundary markers therefore remain attached to the object ID, for example `1700000000 <OID` or `1700000000 -OID`.

`--count` continues to suppress commit presentation entirely, so combining it with `--timestamp` prints only the count. In `--objects` mode only commit records receive timestamps; trees, blobs, and pathname annotations are unchanged. Native `--objects-edge` leading edge records remain untimestamped.

The timestamp is taken from the commit's committer identity, matching Git's raw commit timestamp semantics. The feature composes with the existing Phase 133–138 parent/child, boundary, side, parent-count, oldest-count, age-filter, shallow and object-selection behavior without changing those selectors.

## Safety

The command is read-only. It does not modify refs, reflogs, indexes, worktrees, loose objects, packs, or commit metadata.

## Compatibility boundary

Pretty formats, raw/header output, graph rendering, path-limited history, reflog walks, message/identity grep filters, and other history-simplification options remain separate future work.
