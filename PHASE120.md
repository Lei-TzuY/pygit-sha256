# Phase 120 — Alternate object databases

Phase 120 turns the previously diagnostic-only `objects/info/alternates` file into a real read path. A pygit repository may now borrow SHA-256 loose and packed objects from other pygit object databases without copying them into its own `.pygit/objects` directory.

## Repository layout

Each non-empty line in:

```text
.pygit/objects/info/alternates
```

names another pygit object directory. Absolute paths are accepted. Relative paths are resolved from the current **object database directory**, matching Git's repository-layout rule rather than resolving from the worktree or from `objects/info`.

Alternate stores may themselves have alternates. Pygit follows the chain in deterministic depth-first order, canonicalizes resolved paths, suppresses duplicate/cyclic visits, and enforces a finite recursion bound. A configured alternate that does not exist is repository metadata corruption and fails loudly instead of being silently ignored.

Because native Git and pygit use different repository/object formats, Phase 120 intentionally implements the repository-local `objects/info/alternates` mechanism only; it does not consume Git's process-wide `GIT_ALTERNATE_OBJECT_DIRECTORIES` environment variable.

## ObjectStore semantics

The visible read set is now:

```text
primary loose/pack/MIDX
  + direct alternate loose/pack/MIDX
  + transitive alternate loose/pack/MIDX
```

The following operations see borrowed objects:

- `ObjectStore.read()`
- Phase 119 `ObjectStore.read_store_bytes()` exact-envelope reads
- `ObjectStore.exists()`
- `ObjectStore.all_shas()`
- `ObjectStore.resolve_prefix()`
- shared revision abbreviation resolution
- `cat-file --batch-all-objects`, including `--unordered`

Pack and multi-pack-index validation is reused independently for every object database. Exact raw reads remain observational and do not materialize a borrowed packed object as loose storage. If an alternate contains a corrupt copy and a later alternate has an independent valid copy, reads may recover from the valid copy. Corruption in the **primary** store remains a hard error and is not hidden by borrowing.

## Ownership boundary

Alternates are read-only from the borrowing repository's perspective:

- `write()` and `write_raw()` always publish into the primary object database;
- `delete()` only removes a primary loose object;
- an object that already exists only in an alternate is still materialized locally when explicitly written;
- deleting that local materialized copy reveals the borrowed copy again without modifying the source repository.

This ownership rule keeps maintenance operations from accidentally mutating another repository simply because its objects are visible.

## Batch enumeration

Git's `cat-file --batch-all-objects` includes alternate object databases. Phase 120 applies the same visibility rule:

- ordered mode globally sorts the deduplicated primary+alternate object IDs;
- `--unordered` walks storage roots in primary-then-alternate order, loose objects before packs within each root, while deduplicating objects stored more than once.

## Regression coverage

`tests/test_phase120.py` covers:

- relative alternate paths and loose-object lookup;
- exact Phase 119 raw-envelope reads from loose and packed alternates;
- packed-only alternate objects through the alternate's MIDX without loose materialization;
- transitive alternates with a cycle back to the primary store;
- missing and NUL-corrupt alternates metadata;
- local write/delete ownership boundaries;
- recovery from a corrupt earlier alternate using a later valid copy;
- primary-corruption precedence over a valid alternate;
- ordered and unordered `cat-file --batch-all-objects` enumeration with cross-store deduplication.
