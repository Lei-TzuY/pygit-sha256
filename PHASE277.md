# Phase 277 — annotated-tag-aware `rev-list -z` object filtering

Phase277 extends Phase274's non-ordered annotated-tag object traversal into the
current Git 2.55 structured `rev-list -z` protocol.

## Scope

Phase274 taught the line/count `object:type` adapter that annotated tags named by
positive revisions are real traversed/provided objects. Before this phase that
adapter deliberately rejected `-z` whenever such a tag object had to be
inserted, because tag placement and naming metadata had not been modelled.

Phase277 closes that gap without adding another commit/tree walker:

- the mature NUL adapter now accepts `object:type=tag` as a membership filter;
- the commit-rooted inventory still does not fabricate tag objects;
- Phase277 discovers the same local positive annotated-tag chains as Phase274;
- the base NUL renderer filters commit/tree/blob/promisor inventory before any
  output or ordinary missing-object validation;
- local tag records are then inserted at Git's object-presentation position;
- an annotated tag's object name is encoded as `path=<tag-name>` metadata;
- `--no-object-names` emits only the local tag OID record;
- `--filter-provided-objects` removes nonmatching provided commit/tag exemptions;
- matching tag objects remain visible because they satisfy `object:type=tag`;
- `--count` remains a normal newline integer and includes inserted tag objects;
- `--filter-print-omitted` remains empty for all `object:type` filters;
- `--boundary` keeps structured `boundary=yes` metadata and native tag ordering;
- `-z + --objects-edge` remains rejected by the existing compatibility guard.

## Git 2.55 protocol

Current Git documents `-z` as records of:

```
<OID> NUL [<token>=<value> NUL]...
```

Git v2.55.0 `builtin/rev-list.c` uses `path=<name>` when `show_object()` has an
object name under NUL mode. Annotated tag objects enter that callback with their
embedded tag name, so a provided tag `v1` is represented as:

```
<tag-sha256>\0path=v1\0
```

The same source keeps `boundary=yes` and `missing=yes` as structured metadata,
and leaves the final `--count` result newline-framed.

A Git 2.47.3 local probe performed during design still emitted the older
line-oriented form for this combination. The repository's current compatibility
target and GitHub Actions runner are Git 2.55.0, so Phase277 deliberately follows
2.55 rather than preserving the older permissive framing.

## Ordering

For a nested positive revision `v2 -> v1 -> commit`:

- `object:type=tag -z v2`: provided commit, outer tag, inner tag;
- adding `--filter-provided-objects`: outer tag, inner tag;
- `object:type=commit -z v1`: matching commit ancestry, then provided tag;
- `object:type=tree|blob -z v1`: provided commit, provided tag, then matching
  snapshot objects;
- `--boundary --max-count=1 object:type=commit -z v1`: selected commit,
  structured boundary commit, then provided tag.

These positions mirror Phase274's line-oriented native behavior while using the
structured NUL metadata protocol.

## SHA-256-native / promisor boundary

Every emitted present commit, tag, tree, and blob is a genuine local 64-hex
SHA-256 identity. Annotated tags are ordinary local `TagObject` instances; no
transport SHA-1 is padded, translated, or promoted into a repository-visible
OID slot.

Filtering remains metadata-first. In a blob-less partial clone, an
`object:type=tag` request drops the unresolved promised blob before ordinary
missing validation, so the command succeeds without fetching merely to classify
the object. When a matching missing blob is explicitly requested through
`object:type=blob --missing=print-info`, the local commit/tag stay 64-hex SHA-256
while the 40-hex native SHA-1 appears only inside its `missing=yes` record.

Tests explicitly prohibit both single-object and batch materialization and assert
unchanged promisor state.

## Coordination

- actual `main` remains far behind the stacked rev-list work;
- base: Phase274 / PR #252 exact-green head
  `359c4b9b5e53c773dccd6295e24b4bfddefdee05`;
- Phase275 is occupied by protocol-v2 object-info work / PR #253 and is not
  modified;
- Phase276 is occupied by the independent promisor-size metadata line and is not
  modified;
- Phase277 was rechecked immediately before branch creation and was free;
- Phase277 is intentionally a Phase274 sibling extension, not a base on the
  unrelated protocol/promisor-size branches.

## Verification

Focused regressions cover:

- nested annotated-tag `path=` records;
- no-object-name records;
- provided-object filtering;
- plain commit roots under `object:type=tag`;
- newline count framing;
- existing commit/tree/blob filters with a provided tag;
- structured boundary ordering;
- empty object:type omission sets;
- partial-clone tag filtering before missing validation;
- explicit native-SHA1 missing records after local tag insertion;
- retained object-edge incompatibility;
- byte-for-byte native SHA-256 Git 2.55 behavior.
