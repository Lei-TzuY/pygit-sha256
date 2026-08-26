# Phase 112 — multi-pack-index alternate object directories

Phase 112 adds Git-style `multi-pack-index --object-dir=<dir>` routing for a repository's configured alternate object stores.

## Command

```bash
pygit multi-pack-index --object-dir=/path/to/objects write
pygit multi-pack-index --object-dir=/path/to/objects verify
pygit multi-pack-index --object-dir=/path/to/objects expire
pygit multi-pack-index --object-dir=/path/to/objects repack --batch-size=0
```

The option is global to the `multi-pack-index` command and composes with `write --preferred-pack`, `write --stdin-packs`, `verify`, `expire`, and `repack`.

## Alternate validation

Without `--object-dir`, behavior is unchanged and `.pygit/objects` is used.

An explicit directory must be the repository's own object database or an entry in `.pygit/objects/info/alternates`. Relative alternate entries are resolved relative to the primary object database, matching Git's repository-layout rule. Arbitrary directories are rejected before MIDX mutation.

Phase 112 intentionally uses the repository's alternates file as the authoritative trust boundary. Environment-only alternate discovery is not added because pygit's object store does not otherwise consume Git's process-level alternate environment variables.

## Maintenance routing

All MIDX paths are derived from the selected object directory:

- `write` and `write --stdin-packs` inspect only `<object-dir>/pack`;
- `verify` validates `<object-dir>/pack/multi-pack-index` and its local pack/index pairs;
- `expire` deletes only redundant pack families inside the selected alternate;
- `repack` reads the selected alternate's verified MIDX sources and installs its generated pack back into that same alternate, never the primary object store.

The MIDX invariant remains unchanged: a MIDX references only packfiles in its own pack directory.

## Safety

Alternate selection is canonicalized before use and an unconfigured directory fails before write, deletion, or repack. Existing Phase 107 full-pack verification and Phase 105/106 mutation safeguards continue to protect `verify`, `expire`, and `repack` after routing.

## Compatibility boundary

Phase 112 adds alternate-object-directory routing only; it does not make the general `ObjectStore` transparently traverse alternates. MIDX bitmaps, `--refs-snapshot`, and incremental MIDX chains remain separate format/features.

## Regression coverage

`tests/test_phase112.py` covers absolute and relative alternate declarations, unconfigured-directory rejection, ordinary write/verify, `--stdin-packs` composition, alternate-only expire, alternate-only repack installation, primary-store isolation, and CLI help.
