# Phase 101 — `update-ref --batch-updates`

Phase 101 adds Git-style partial-success reference transactions to the modern `update-ref --stdin` path. The default transaction mode remains all-or-nothing; partial success is opt-in with `--batch-updates`.

## CLI

```bash
printf '%s\n' \
  'update refs/heads/good <new> <expected-old>' \
  'update refs/heads/stale <new> <wrong-old>' \
  | pygit update-ref --stdin --batch-updates
```

A stale compare-and-swap or another rejectable ref-state conflict does not discard unrelated valid updates. Rejections are written as line-delimited records:

```text
rejected <ref> <new-value> <old-value> <reason>
```

This diagnostic stays LF-delimited even when stdin uses `-z`, matching current Git's reporting path.

`--batch-updates` requires `--stdin` and composes with both the LF and NUL protocols, `option no-deref`, direct-ref commands, symbolic-ref commands, and Phase 98 transaction controls.

## Failure boundary

Partial success is deliberately limited to ref-transaction conflicts that correspond to Git's non-generic transaction errors, including incorrect old values, create-existing conflicts, expected-symref failures, and conflicting updates to the same physical ref.

Protocol/syntax errors, invalid object expressions, invalid refnames, branch target-type violations, and system/I/O failures remain fatal. In particular, a storage failure is never converted into a per-ref rejection: surviving updates still pass through the ordinary transaction publisher and its snapshot rollback boundary.

This distinction mirrors current Git's `REF_TRANSACTION_ALLOW_FAILURE` design: user-state conflicts may be rejected individually, while generic/system errors fail the transaction. Git's current implementation also reports `rejected` records through a newline-oriented output path independently of `-z` input framing.

## Explicit transactions

`start`, `prepare`, `commit`, and `abort` retain the Phase 98 lifecycle. Rejections belong to the active transaction: they become reportable when that transaction commits, while `abort` or an explicit transaction reaching EOF discards both pending updates and its pending rejections.

`prepare` still has the existing pygit boundary documented in Phase 98: it performs complete in-process preflight but does not claim Git's cross-process ref-backend lockfile semantics.

## Implementation

`pygit.ref_batch` contains the partial-success policy layer. It incrementally validates each candidate against the surviving transaction, removes only rejectable conflicts, and delegates final publication to the existing mixed direct/symbolic `_apply_updates` transaction path. The modern `update_ref_cli` adapter keeps normal `update-ref` behavior unchanged while exposing the new flag through the installed CLI.

## Regression coverage

`tests/test_phase101.py` covers:

- stale CAS rejection with unrelated successful publication;
- create-existing rejection;
- duplicate physical-ref conflict handling;
- explicit commit/abort and EOF behavior;
- fatal malformed object input preserving all-or-nothing behavior;
- fatal system/I/O failure propagation;
- installed CLI rejection records and successful exit status;
- `-z` input with LF rejection diagnostics;
- `--batch-updates` grammar and help output.
