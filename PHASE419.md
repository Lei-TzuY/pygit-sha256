# Phase419: integrate previous-checkout porcelain siblings

Phase419 cleanly integrates the three exact-green previous-checkout selector lines that were developed independently from Phase414/415:

- Phase416 / PR #377: `rev-parse @{-N}` and ref-aware output modes;
- Phase417 / PR #379: `branch -c/-C @{-N} <new>` copy semantics;
- Phase418 / PR #380: `branch -m/-M @{-N} <new>` move and forced-move semantics.

The exact Phase418 head is used as the base so Phase415's fail-closed rename implementation and Phase418's forced destination replacement stay intact. Phase416 and Phase417 modules, focused tests, and design notes are then retained byte-for-byte. The only shared production conflict is the top-level `pygit.application` router, which is merged once in this phase.

## Integrated routing contract

The application router now owns these focused previous-selector shapes together:

- `rev-parse @{-N}`;
- `rev-parse --verify [-q|--quiet] @{-N}`;
- `rev-parse --abbrev-ref @{-N}`;
- `rev-parse --symbolic-full-name @{-N}`;
- `branch -c/--copy/-C @{-N} <new>` and explicit copy+force long/short forms;
- `branch -m/--move/-M @{-N} <new>` and explicit move+force forms;
- the previously established branch-create and checkout previous-selector forms.

Unsupported rev-parse options, ordinary revisions, literal branch operands, extra-argument forms, and all other command grammar remain legacy-owned. Integration therefore adds no new broad interception rule.

## Cross-feature semantics

A previous checkout selector is checkout-history metadata, not an object identity. Branch copy does not mutate HEAD checkout history, so resolving `@{-1}` before and after `branch -c @{-1} copy` returns the same genuine local SHA-256 tip. A later `branch -M @{-1} moved` renames the selected source branch while the copied branch keeps the same tip.

The Phase419 regression verifies the sequence end-to-end in pygit and independently against native SHA-256 Git:

```text
main -> topic -> main
rev-parse @{-1}
branch -c @{-1} copy
rev-parse @{-1}
branch -M @{-1} moved
```

Expected final state:

- `copy` and `moved` both reference the original topic commit;
- the original `topic` ref no longer exists;
- HEAD remains on `main`;
- every local object/ref identity remains a genuine 64-hex SHA-256 OID.

## Exact sibling preservation

Phase419 intentionally reuses the already-green sibling content instead of rewriting it:

- Phase416 head: `89844f0f87f8555d2cbb0d959a8c75d6a32ffeea`;
- Phase417 head: `ea39ba84d41cdc628f779673e326b917ee39d283`;
- Phase418 base/head for this integration: `d8b373f2d33a081c0297891149c1e14e3c54df69`.

The Phase416/417 modules, tests, and documentation are expected to retain their source blob identities. Only `pygit/application.py` is intentionally synthesized from the three routing deltas.

## SHA-256-native invariants

This integration changes only local porcelain/ref/config/reflog/revision routing behavior already implemented by its component phases. It introduces no object serialization, packfile, transport, promisor, object-map, FETCH_HEAD, shallow-state, or storage-format change. Selector text is metadata only and is never padded, truncated, rehashed, or converted into a surrogate identity.

## Coordination

- exact base: Phase418 / PR #380 head `d8b373f2d33a081c0297891149c1e14e3c54df69`;
- Phase418 GitHub Actions run `33552315304`: success on Python 3.9 and 3.13 with Git 2.55.0;
- Phase416 and Phase417 are independently green siblings;
- Phase419 namespace was collision-checked immediately before branch creation;
- unrelated protocol, bundle, clone, durability, and later branch-porcelain lines are untouched.

This phase intentionally remains a stacked, open, unmerged pull request.
