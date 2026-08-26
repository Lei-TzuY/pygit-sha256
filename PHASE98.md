# Phase 98 — `update-ref --stdin` transaction control

Phase 98 extends the existing Phase 51 direct-reference transaction engine with Git-style session controls for script-driven `update-ref --stdin` workflows.

## Supported control records

```text
start
prepare
commit
abort
option no-deref
```

These compose with the existing direct-ref records:

```text
update <ref> <new> [<old>]
create <ref> <new>
delete <ref> [<old>]
verify <ref> [<old>]
```

Example:

```text
start
verify refs/heads/main <expected-main>
create refs/heads/release <new-tip>
prepare
commit
```

`start` turns the current stdin transaction into an explicit transaction. `prepare` performs complete object/ref/CAS validation without publishing changes. `commit` publishes the queued transaction using the existing atomic loose-ref replacement, packed-ref deletion, reflog, and rollback path. `abort` discards the queued transaction.

An implicit session remains backward compatible: direct update records with no transaction controls commit automatically at EOF. A transaction that has been explicitly started or prepared is discarded at EOF unless an explicit `commit` occurs. Multiple transactions may be committed or aborted in one stdin session.

## `option no-deref`

`option no-deref` affects only the next command that names a ref. This permits a transaction to mix ordinary symbolic-ref dereferencing with a one-off direct update of the symbolic ref itself. The command-line `--no-deref` option remains the session-wide default.

## Zero-OID update deletion

`update <ref> <zero-oid> [<old>]` now removes the ref, matching Git's direct-ref stdin semantics. `create` still rejects a zero new object ID.

## Atomicity and prepare boundary

Phase 98 reuses the existing transaction publication path: all ref/object/CAS checks happen before mutation, replacement files are prepared first, packed backing values are removed consistently for deletions, reflogs are updated after ref publication, and I/O failures restore snapshotted ref/reflog/packed-ref files.

`prepare` in pygit is a complete preflight barrier but does **not** claim Git's cross-process ref-backend lockfile semantics. Commit revalidates immediately before publication. This keeps the implementation correct within pygit's current storage model instead of pretending to provide concurrency guarantees its ref backend does not yet implement.

## Scope boundary

This phase deliberately covers the complete control protocol for the already-supported direct-ref commands. Native `symref-update` / `symref-create` / `symref-delete` / `symref-verify`, `-z` NUL-framed `update-ref --stdin`, and `--batch-updates` rejection reporting remain separate work.

## Regression coverage

`tests/test_phase98.py` covers implicit EOF commit, explicit EOF abort, multiple transactions in one session, abort isolation, prepare-without-publish, prepare failure atomicity, prepared-state closure rules, one-shot and global no-deref behavior, zero-OID update deletion, parser errors, and installed command dispatch.
