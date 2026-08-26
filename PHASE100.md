# Phase 100 — `update-ref --stdin` symbolic-ref transactions

Phase 100 completes the symbolic side of the modern `update-ref --stdin` protocol. Direct refs and symbolic refs now share one transaction planner and publisher instead of requiring separate commands outside the transaction boundary.

## Supported commands

Line-delimited and `-z` NUL-delimited stdin modes both support:

```text
symref-update <ref> <new-target> [ref <old-target> | oid <old-oid>]
symref-create <ref> <new-target>
symref-delete <ref> [<old-target>]
symref-verify <ref> [<old-target>]
```

These compose with the existing `update`, `create`, `delete`, `verify`, `option no-deref`, `start`, `prepare`, `commit`, and `abort` commands.

`option no-deref` remains one-shot. In normal dereference mode a `symref-update` follows an existing symbolic ref and replaces its physical referent with a symbolic ref, matching Git's transaction semantics. Supplying `option no-deref` immediately before the command updates the named symbolic ref itself. `symref-delete` and `symref-verify` require no-deref mode, as native Git does.

## Validation and transaction behavior

Symbolic updates validate full ref names and optional compare-and-swap conditions before publication. `symref-update` may compare either an old symbolic target (`ref <old-target>`) or the dereferenced object value (`oid <old-oid>`). `symref-create` requires non-existence, while `symref-verify` without an old target also verifies non-existence.

The planner detects duplicate physical refs across direct and symbolic operations and evaluates projected symbolic targets across the complete transaction. A cycle created only by two or more queued symbolic updates is therefore rejected before any ref file is published.

Mixed transactions use the same temp-file preparation, snapshot rollback, packed-ref cleanup, and reflog path as direct updates. Symbolic writes remove stale packed backing values. `prepare` performs the full mixed preflight but, as documented in Phase 98, does not claim Git's cross-process lockfile guarantees; `commit` revalidates before publication.

## NUL framing

`update-ref --stdin -z` parses symbolic fields without text splitting. `symref-update` consumes a required target and optionally a `ref`/`oid` discriminator plus its value. `symref-delete` and `symref-verify` retain the protocol's explicit empty NUL field for a missing optional old target, so truncated streams are rejected instead of being reinterpreted.

## Regression coverage

`tests/test_phase100.py` covers line parsing, mixed direct/symbolic commits, rollback on symbolic CAS failure, dereference vs `option no-deref`, symbolic delete/verify rules, projected-cycle rejection, prepare/abort behavior, NUL old-target and old-OID parsing, installed CLI mixed transactions, and truncated NUL records.
