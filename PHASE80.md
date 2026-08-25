# Phase 80 — exact `show-ref --exists`

Phase 80 extends the Phase 79 local reference inspector with the dedicated existence-query form used by scripts.

## Added behavior

- `pygit show-ref --exists <ref>` accepts exactly one fully-qualified `refs/...` name.
- Exit status `0`: the ref record exists.
- Exit status `2`: the ref is absent.
- Exit status `1`: the lookup could not be completed because the ref name or ref storage is invalid/corrupt.
- Successful and missing lookups are silent.
- Existence is storage-oriented rather than object-oriented: a loose or packed ref can exist even when its recorded object is unavailable, and a dangling symbolic ref still counts as an existing ref record.
- Loose refs shadow packed refs naturally; packed-only refs are supported through the strict packed-ref parser.
- `--exists` is a standalone mode and rejects listing, filtering, formatting, dereference, quiet, and verification options instead of silently assigning mixed semantics.

## API

`pygit.ref_exists(repo, refname) -> bool` exposes the same exact-ref storage query to Python callers. Invalid names and corrupt packed-ref storage raise rather than being collapsed into `False`, preserving the CLI's `1` versus `2` distinction.

## Verification

`tests/test_phase80.py` covers:

- loose refs whose target object is missing;
- dangling symbolic refs;
- packed-only refs with unavailable objects;
- missing refs and tri-state CLI exit codes;
- unsafe/non-qualified ref names;
- malformed `packed-refs` lookup failures;
- option/multiplicity validation;
- silent success/missing behavior.

This phase intentionally does not add `show-ref --exclude-existing`, whose stdin filtering and refname-validation semantics are distinct enough to deserve a separate focused implementation.
