# Phase 213 — Lazy promisor object materialization

Phase213 builds directly on Phase212's partial-fetch/promisor storage model. A
filtered repository may now consume an omitted blob without inventing a fake
SHA-256 identity and without rewriting its parent tree after the blob arrives.

## What changed

- `TreeEntry` can carry a runtime-only native-object resolver.
- Reading a native-reference foreign tree remains network-free.
- Accessing an unresolved `TreeEntry.sha` asks the recorded promisor remote for
  exactly that native SHA-1 object over smart-HTTP protocol v2.
- The materialization request deliberately omits the repository's partial-clone
  filter so the requested object is actually returned.
- Existing ordered `remote.<name>.serverOption` values are forwarded to the v2
  command.
- The received native object is imported through the established
  native-Git-to-pygit boundary and receives its real content-derived local
  SHA-256 identity.
- `.pygit/promisor.json` atomically moves that native oid from `promised` to
  `resolved`; subsequent reads are metadata-only and perform no network fetch.
- A materialization response is rejected if it unexpectedly attempts to alter
  shallow repository state.
- Phase212's single-promisor-remote boundary is preserved. If metadata becomes
  ambiguous, materialization fails instead of guessing which remote owns a
  promise.

## Stable identity

Filtered foreign trees continue to serialize only their original native SHA-1
entry identities under the `pygit-native-tree-v1` representation. The lazy
resolver is ephemeral and is not serialized. Therefore materializing a promised
blob changes neither the tree payload nor the tree's repository-visible SHA-256
object id.

This preserves the central SHA-256-native rule:

- SHA-1 is used only to identify the promised object at the Git interoperability
  boundary;
- the materialized blob is stored and referenced by its real 64-hex pygit
  SHA-256 identity;
- no surrogate local ids are created.

## Git compatibility scope

Git partial clone treats the configured promisor remote as the source for
objects intentionally omitted by a filter and lazily fetches missing objects by
object id when they are required. Phase213 implements that object-level
materialization primitive for Phase212's promised blobs.

This phase does not yet expose `clone --filter`: clone must also integrate the
same lazy path with initial checkout and failure/rollback semantics. With the
object materializer now present, that becomes a contained follow-up rather than
an object-model problem.

## Verification

`tests/test_phase213.py` covers:

- resolver laziness and per-entry caching;
- real SHA-256 blob import and promisor-state transition;
- no second network transfer after resolution;
- native tree identity stability before/after materialization;
- unpromised-object refusal;
- ambiguous promisor-remote refusal;
- exact protocol-v2 `want <native-sha1>` request framing;
- filter omission during the object-level materialization request;
- server-option forwarding.
