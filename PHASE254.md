# Phase254 — `rev-list --filter-print-omitted --count`

Phase254 composes the Phase253 omitted-object presentation with the structured object counts introduced by the earlier filter phases. Phase255 later corrected the filter-specific omitted-set semantics for `object:type`.

## Behavior

Supported line-oriented forms include:

```text
pygit rev-list --objects --count --filter=blob:none --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --count --filter=object:type=tree --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --count --filter=object:type=tree --filter-provided-objects --filter-print-omitted --missing=allow-promisor HEAD
```

The final integer counts only present objects that survive the active object filter. Objects actually collected by the filter's omitted-object set are advertised separately as `~<oid>` and do not contribute to that count.

For Git 2.55 compatibility, this distinction is filter-specific: `blob:none` collects filtered blobs and therefore emits `~` records, while `object:type=...` does not populate the omitted set. Object-type counts still change normally, including with `--filter-provided-objects`, but no synthetic `~` records are emitted for the rejected types.

## Native Git ordering

Current Git's `builtin/rev-list.c` performs filtered traversal, then prints the collected omitted-object set, then prints collected missing-object diagnostics, and only after that prints the final `--count` value. Phase254 follows the same presentation ordering:

1. any non-count traversal framing that survives projection;
2. `~<oid>` records actually collected by the active filter;
3. `?` missing-object diagnostics, when requested;
4. the final integer count.

The underlying Phase247/250 structured filter-count implementations remain authoritative for selection and counting; the omitted adapter rearranges presentation rather than deriving counts from printed lines.

## Phase255 compatibility correction

The original Phase254 regressions expected `object:type=tree --filter-print-omitted` to report filtered commits and blobs. Git 2.55 `filter_object_type()` leaves its `omits` parameter unused, so those expectations were incorrect. Phase255 updated the tests: object-type count values remain unchanged, but the omitted set is empty both with and without `--filter-provided-objects`.

## SHA-256-native boundary

The omitted-object channel remains a repository object-id channel. Every emitted `~<oid>` is therefore a genuine local 64-hex SHA-256. If an unresolved promised object itself would need to be reported by a filter that collects omissions, pygit still fails explicitly because only its foreign/native transport SHA-1 is known before materialization. No padding, translation, or surrogate SHA-256 is introduced.

## Regression coverage

Phase254/255 coverage checks `blob:none` omitted records followed by the filtered count, object-type counting with and without the provided-root exemption, native-empty object-type omitted sets, and the ordering contract for omitted/missing/final-count records.
