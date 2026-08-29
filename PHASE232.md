# Phase 232 — Promisor-aware object inventory

Phase232 adds a metadata-only reachable-object inventory for partial-clone repositories. The new `pygit.promisor_object_inventory.promisor_object_inventory()` API is deliberately lower-level than CLI formatting: it records what is locally materialized and what is still promised without triggering lazy network fetches.

## Why this layer is needed

Git's partial-clone documentation describes `rev-list --objects --missing=...` as an important primitive for discovering missing objects before another command bulk-prefetches them. In a native Git repository, the missing object's repository object id is already known from its containing tree.

pygit has an additional hash-domain boundary. Repository-visible objects are SHA-256, while filtered foreign trees preserve the upstream Git SHA-1 entry identity until omitted content is received. A missing blob's *correct* local SHA-256 id therefore cannot be known before its contents are materialized.

Phase232 does not weaken that invariant. It represents inventory entries as:

- `oid`: the real local SHA-256 id, only when the object is materialized;
- `native_oid`: the upstream/promisor SHA-1 identity, only while the object remains unresolved;
- `missing`: derived from the absence of a local `oid`;
- `type_name` and the first stable tree `path` when available.

No synthetic SHA-256 ids are created and native SHA-1 is not relabeled as a pygit object id.

## Traversal semantics

`promisor_object_inventory()` reuses the existing `rev_list()` commit selector, so ordinary positive/negative revisions, ranges, shallow-aware commit selection, first-parent selection, ordering, skip, and max-count stay aligned with existing rev-list behavior.

For each selected commit it then walks locally available tree metadata:

- local commits and trees remain normal 64-hex SHA-256 inventory entries;
- resolved blobs/trees/commits use their real local SHA-256 ids;
- unresolved entries that are recorded in `.pygit/promisor.json` are reported as missing with their native identity and are not dereferenced;
- an unresolved entry that is *not* a recorded promise keeps the historical error behavior instead of being silently treated as a valid partial-clone omission;
- duplicate objects retain their first stable pathname;
- explicit negative revision closures (and common ancestry for symmetric ranges) are subtracted from the selected object inventory.

The API performs no protocol request, no promisor-state mutation, no index/worktree/ref mutation, and no object materialization.

## Git compatibility direction

Current Git documents these missing-object modes for `rev-list`: `error` (default), `allow-any`, `allow-promisor`, `print`, and `print-info`. It also documents missing-object enumeration as a way for higher-level commands to plan one bulk fetch instead of faulting objects in one at a time.

Phase232 provides the hash-safe object-graph substrate for those CLI modes. A follow-up can map the structured inventory onto Git-compatible presentation where the hash domains permit it, while keeping native transport identities explicit when a true local SHA-256 id is not yet derivable.

## Tests

`tests/test_phase232.py` covers:

1. a real filtered foreign `blob:none` fixture whose three blobs remain promised;
2. proof that inventory performs neither single-object nor batch network fetches;
3. unchanged promisor state before/after traversal;
4. local commit/tree objects remaining 64-hex SHA-256 while missing blobs remain explicit 40-hex native identities with `oid=None`;
5. ordinary repositories producing only local SHA-256 entries;
6. negative revision object-closure subtraction (`base..tip`) so shared historical content is not reintroduced into the selected inventory.

## Coordination

Phase231 / PR #208 was the latest green stacked work when Phase232 started. No Phase232 branch existed. This phase is based exactly on Phase231 head `0bf6dcfe8870dc7550bc3dfa7c6af2be8d11665d` and intentionally remains unmerged.
