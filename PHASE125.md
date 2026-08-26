# Phase 125 — alternate-backed multi-stage index integration

Phase 125 locks the integration boundary between Phase 123 alternate object databases and Phase 124 persistent index conflict stages. A multi-stage index may name objects that exist only in a configured alternate object database; those borrowed objects must remain readable through normal revision and `cat-file` plumbing without being materialized into the primary store.

## Borrowed conflict stages

Stages 1, 2, and 3 are object IDs, not ownership claims. `update-index --index-info` therefore validates them through the repository's complete visible object set:

```text
primary object database + configured alternates
```

A borrower can persist, reload, and resolve:

```text
:1:path   merge-base object from an alternate
:2:path   ours object from an alternate
:3:path   theirs object from an alternate
```

The same entries are visible through `ls-files --stage`, the shared revision resolver, and `cat-file -p`.

## Packed alternates

The integration also covers objects that are packed-only in the alternate and indexed by that alternate's multi-pack-index. Looking up conflict stages follows Phase 123's normal object-store read path; it does not create primary loose copies merely because an OID appears in the index.

## Resolution ownership

Normal worktree staging retains Phase 124's conflict-resolution rule: stages 1-3 are removed and one stage-0 entry is created from the resolved worktree bytes. That new object is written to the borrower's **primary** object database. The alternate remains read-only and is not modified.

After this resolution, the path no longer depends on its former borrowed stage objects. Removing the alternates configuration still leaves the locally resolved stage-0 entry readable.

## Why this phase exists

Phase 123 and Phase 124 were developed in parallel and touched different files, but they meet at `ObjectStore`: index stage validation and revision resolution read the objects that alternates make visible. Phase 125 adds explicit cross-phase regressions so later storage or index changes cannot silently break that composition.

## Regression coverage

`tests/test_phase125.py` covers:

- stages 1-3 whose blobs exist only as loose objects in an alternate;
- persistent reload and shared `:N:path` revision lookup;
- `ls-files --stage` and `cat-file -p` over borrowed conflict stages;
- packed-only alternate conflict objects through a multi-pack-index;
- absence of accidental primary loose-object materialization during reads;
- resolving an alternate-backed conflict into one locally owned stage-0 object;
- alternate read-only ownership and independence after conflict resolution.
