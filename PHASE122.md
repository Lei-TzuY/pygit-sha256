# Phase 122 — Alternate object databases

Phase 122 turns `objects/info/alternates` into a real read path. A pygit repository may borrow SHA-256 loose and packed objects from other pygit object databases without copying them into its own `.pygit/objects` directory.

## Repository layout

Each non-empty line in `.pygit/objects/info/alternates` names another pygit object directory. Absolute paths are accepted. Relative paths are resolved from the current object database directory, matching Git's repository-layout rule rather than resolving from the worktree or from `objects/info`.

Alternate stores may themselves have alternates. Pygit follows the chain in deterministic depth-first order, canonicalizes resolved paths, suppresses duplicate/cyclic visits, and enforces a finite recursion bound. A configured alternate that does not exist is repository metadata corruption and fails loudly instead of being silently ignored.

Because native Git and pygit use different object formats, Phase 122 intentionally implements repository-local `objects/info/alternates` only; it does not consume Git's process-wide `GIT_ALTERNATE_OBJECT_DIRECTORIES` variable.

## ObjectStore semantics

The visible read set is primary loose/pack/MIDX plus direct and transitive alternate loose/pack/MIDX storage. Borrowed objects participate in `ObjectStore.read()`, exact `read_store_bytes()`, `exists()`, `all_shas()`, `resolve_prefix()`, shared revision abbreviation resolution, and `cat-file --batch-all-objects` including `--unordered`.

Pack and multi-pack-index validation is reused independently for every object database. Exact raw reads do not materialize borrowed packed objects as loose storage. If an alternate contains a corrupt copy and a later alternate has an independent valid copy, reads may recover from the valid copy. Corruption in the primary store remains authoritative and is not hidden by borrowing.

## Ownership boundary

Alternates are read-only from the borrowing repository's perspective. `write()` and `write_raw()` always publish into the primary database, while `delete()` only removes a primary loose object. An object that exists only in an alternate is materialized locally when explicitly written; deleting that local copy reveals the borrowed copy again without modifying the source repository.

## Batch enumeration

`cat-file --batch-all-objects` includes alternate databases. Ordered mode globally sorts the deduplicated primary+alternate object IDs. `--unordered` walks storage roots in primary-then-alternate order, loose objects before packs within each root, while deduplicating objects stored more than once.

## Regression coverage

`tests/test_phase122.py` covers relative paths, loose and packed-only alternates, per-alternate MIDX lookup, exact stored bytes, transitive cycles, malformed metadata, local write/delete ownership, redundant-copy recovery, primary-corruption precedence, packed-aware short IDs, and ordered/unordered batch enumeration.
