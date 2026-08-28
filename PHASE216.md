# Phase 216 — Promisor-aware checkout batching

Phase216 makes later checkout of a partial clone use the batched promisor materialization path instead of falling back to one object fetch per file.

## Motivation

Phase213 added lazy single-object materialization, Phase214 added batched materialization for the initial filtered-clone checkout, and Phase215 added `clone -n` / `--no-checkout` so a `blob:none` clone can initially leave every worktree blob promised.

Without another integration point, a later `pygit checkout <branch>` would consume unresolved `TreeEntry.sha` values one at a time and therefore issue one protocol-v2 object request per file. Phase216 batches those promises before the established checkout implementation starts flattening the tree.

## Behavior

`Repository.checkout()` is wrapped transparently at package initialization.

For a non-orphan checkout:

1. read durable promisor state;
2. if no promised objects remain, delegate immediately with no tree walk or network activity;
3. resolve the checkout target using the same ref resolver as the historical implementation;
4. walk the already-present commit/tree graph and collect unresolved promised blob native SHA-1 ids;
5. call `materialize_promised_objects()` once with the complete deduplicated set;
6. delegate to the original checkout implementation unchanged.

The original checkout continues to own sparse filtering, worktree writes, index rebuilding, HEAD/reflog changes, detached-HEAD behavior and the `post-checkout` hook.

Ordinary repositories do not enter the materializer. Orphan checkout does not materialize anything because it restores no commit tree. Unknown revisions are left to the original checkout error path.

## Identity and protocol boundary

Phase216 does not introduce any new object format. Foreign trees continue to store native Git SHA-1 entry identities through the Phase212 canonical representation. Materialized blobs are written under their real local SHA-256 identities and persistent native-to-local resolutions are updated by the existing Phase214 materializer. Parent tree and commit SHA-256 ids remain stable.

## Compatibility

The `Repository.checkout(target, orphan=False)` signature and return behavior are unchanged. The integration is installed as an idempotent repository extension, following the existing merge/replay/promisor extension pattern rather than duplicating the large checkout implementation.

The Phase213 single-object seam remains intact: a target with exactly one unresolved promised object still uses `_fetch_native_object`; targets with multiple unresolved objects use `_fetch_native_objects` once.

## Verification targets

- two promised checkout blobs are fetched in one batch and never use the single-object transport;
- one promised blob preserves the Phase213 `_fetch_native_object` compatibility seam;
- ordinary checkout does not invoke promisor materialization;
- orphan checkout leaves promises untouched;
- unknown-revision errors remain authoritative from the original checkout path;
- full Python 3.9 / 3.13 regression suite remains green.
