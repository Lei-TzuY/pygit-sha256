# Phase248: NUL-framed `rev-list --filter=blob:none`

Phase248 composes the Phase244/245 NUL object-record protocol with the
metadata-only `blob:none` object filter introduced in Phase246 and refined in
Phase247.

## Goal

Support Git-style object filtering without converting the NUL protocol back into
line-oriented text:

- `rev-list --objects -z --filter=blob:none <revisions>`
- the same form with `--missing=allow-promisor`
- the same form with `--missing=print`
- the same form with `--missing=print-info`
- `--boundary`, `--skip`, `--max-count`, `--reverse`, and the existing ordinary
  NUL traversal where supported by the underlying adapter

`--count` and `--objects-edge` remain rejected under `-z`; Phase248 does not
invent a mixed NUL framing for options that the existing NUL contract already
treats as incompatible.

## Git compatibility

Current `git-rev-list` documentation defines `--filter=blob:none` as omitting all
blobs from an `--objects*` traversal. It separately defines `-z` as a structured
object record protocol:

```
<OID> NUL [<token>=<value> NUL]...
```

with metadata such as `path=...`, `boundary=yes`, and `missing=yes`. These
features are orthogonal: the filter chooses which objects remain in the object
set, while `-z` controls how the surviving objects are represented.

Phase248 therefore filters structured inventory entries before any NUL record is
emitted. It does not parse NUL output after the fact.

## Implementation

`rev_list_nul_cli.try_run_rev_list_nul()` gains one keyword-only presentation
hook:

- `omit_blobs=False` preserves all Phase244/245 behavior for existing callers.
- `omit_blobs=True` removes inventory entries whose `type_name == "blob"` before
  `_emit_entries()` runs.

The Phase248 filter adapter removes `--filter=blob:none` from the projected
argument list and delegates directly to the NUL adapter with
`omit_blobs=True`.

This has several important consequences:

1. already-local blob records disappear without decoding NUL fields;
2. unresolved promised blob entries disappear before `_emit_missing()`, so they
   neither fetch nor emit `missing=yes`;
3. ordinary `-z --filter=blob:none` can safely traverse a `blob:none` partial
   clone without an explicit missing policy when every unresolved entry selected
   by the filter is a blob;
4. commit/tree identities, `boundary=yes`, and non-blob `path=` metadata remain
   byte-for-byte owned by the existing NUL renderer;
5. newline-containing paths remain verbatim `path=` token values rather than
   being quoted or truncated.

## SHA identity boundary

Phase248 preserves the repository's dual-domain rule:

- surviving present commit/tree identities are genuine local 64-hex SHA-256;
- a filtered promised blob emits no identity at all;
- unresolved non-blob promises, if encountered under a supported explicit
  missing mode, remain confined to records marked `missing=yes`;
- no native SHA-1 is promoted into the repository-visible SHA-256 domain;
- no surrogate SHA-256 is invented.

## Network and mutation guarantees

The entire path remains metadata-only:

- zero single-object promisor fetches;
- zero batch promisor fetches;
- no worktree/index/ref mutation;
- no promisor-state mutation.

## Regression coverage

Focused tests cover:

- an ordinary repository where a local blob is omitted while commit/root/subtree
  SHA-256 records remain;
- a newline-containing directory path whose non-blob subtree `path=` metadata is
  preserved verbatim;
- a real foreign `blob:none` partial clone under ordinary NUL mode and each of
  `allow-promisor`, `print`, and `print-info`;
- suppression of promised blob `missing=yes` / `type=blob` records with zero
  network access and unchanged promisor state;
- `--boundary --max-count=1`, proving `boundary=yes` and both non-blob snapshot
  trees survive filtering;
- continued NUL rejection of `--count` and `--objects-edge`.

Phase248 changes no object format, tree serialization, pack format, wire
protocol, ref/index/worktree format, or promisor identity representation.
