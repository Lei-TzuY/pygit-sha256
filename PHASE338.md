# Phase 338 — Complete fully-known incremental fetches

Phase338 closes the final repository-side gap in the Phase333–336 mapped
incremental packfile-URI path: a fully up-to-date fetch may legitimately contain
no new native objects at all.

## Native behavior

A `have` tells the upload-pack server that the client already has the object and
allows the server to omit objects reachable from it. Native Git can therefore
produce a valid zero-object incremental pack. The Phase338 differential probe runs
`git pack-objects --stdout --revs` with the same commit as both inclusion and
exclusion (`<tip>` / `^<tip>`) and observes a valid 32-byte PACK v2 with object
count zero and a normal trailer.

Current protocol-v2 documentation likewise defines `have` as local object
knowledge used to build a pack containing only objects the client needs, while
the pack format encodes its object count as an unsigned 4-byte field.

## Staging semantics

`stage_packfile_uri_import()` still rejects an empty fetched object set by
default. Phase338 allows it only when `known_native_to_local` is non-empty and the
existing Phase334 validator has successfully re-read every mapped local object and
verified its full SHA-256 identity.

The result is deliberately:

```text
StagedPackfileUriImport({}, ())
```

Known objects are not copied, republished, or reported as newly staged objects.
They are evidence for already-present immutable content.

## Root certification

`certify_packfile_uri_roots()` gains an optional `known_native_to_local` fallback.

For each expected remote-native root:

1. a staged mapping wins when present and retains Phase322's existing
   newly-published-object requirement;
2. otherwise the root must exist explicitly in the supplied known mapping;
3. the selected full local SHA-256 object is re-read from the destination store;
4. its content-derived SHA-256 and expected Git object type are checked again;
5. staged and known mappings for the same root must agree exactly.

There is no implicit lookup, SHA-1 truncation/padding, or surrogate identity.

## Incremental transaction

Phase336's ordering becomes:

```text
download -> stage -> [new immutable LMAP] -> certify -> guarded CAS refs
```

The LMAP step is conditional. If staging produced new native/local mappings, they
are persisted exactly as Phase336 requires. If the response was fully known and
staging is empty, `object_map` is `None` and no redundant or empty map generation
is created.

Certification receives the exact same Phase333 incremental known map that was
used by staging. Ref publication still performs the existing expected-old CAS.
For an up-to-date tracking ref, old and new local SHA-256 values are identical;
the ref backend therefore records no reflog entry while still detecting a
concurrent ref move.

## Failure model

- empty fetched set + empty known map: reject as before;
- expected root absent from both staged and known mappings: reject;
- known local object missing, unreadable, wrong SHA-256, or wrong Git type: reject;
- staged/known disagreement for one native root: reject;
- CAS race after certification: reject without partial ref success.

No known-only path creates new object-store contents or compatibility-map files.

## Exact base

Phase338 is stacked on Phase336 / PR #313 exact-green head:

`326a7db6981e19b0435a3cf7868a39d9eaf3885b`

Phase336 authoritative GitHub Actions Tests #2880 passed with 2541 tests on both
Python 3.9 and Python 3.13 using Git 2.55.0.

Phase337 is independently occupied by the unborn-clone line, so this work uses
Phase338 after a collision recheck.

## Verification

`tests/test_phase338.py` covers:

- empty staging rejection without known state;
- validated known-only empty staging;
- known-root certification and type rejection;
- complete known-only transaction with no new LMAP and no no-op reflog entry;
- expected-root absence failure;
- native Git zero-object PACK generation and parsing.

The complete GitHub Actions Python 3.9 / 3.13 suite remains the authoritative
gate. This PR must remain open and unmerged unless explicitly requested.
