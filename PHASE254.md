# Phase254 — `rev-list --filter-print-omitted --count`

Phase254 composes the Phase253 local-SHA-256 omitted-object channel with the structured object counts introduced by the earlier filter phases.

## Behavior

Supported line-oriented forms now include:

```text
pygit rev-list --objects --count --filter=blob:none --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --count --filter=object:type=tree --filter-print-omitted --missing=allow-promisor HEAD
pygit rev-list --objects --count --filter=object:type=tree --filter-provided-objects --filter-print-omitted --missing=allow-promisor HEAD
```

The final integer counts only present objects that survive the active object filter. Omitted objects are advertised separately as `~<oid>` and do not contribute to that count.

## Native Git ordering

Current Git's `builtin/rev-list.c` performs filtered traversal, then prints the collected omitted-object set, then prints collected missing-object diagnostics, and only after that prints the final `--count` value. Phase254 follows the same presentation ordering:

1. any non-count traversal framing that survives projection;
2. `~<oid>` omitted records;
3. `?` missing-object diagnostics, when requested;
4. the final integer count.

The underlying Phase247/250 structured filter-count implementations remain authoritative for selection and counting; Phase254 rearranges presentation rather than deriving counts from printed lines.

## SHA-256-native boundary

The omitted-object channel remains a repository object-id channel. Every `~<oid>` is therefore a genuine local 64-hex SHA-256. If an unresolved promised object itself would need to be reported as omitted, pygit still fails explicitly because only its foreign/native transport SHA-1 is known before materialization. No padding, translation, or surrogate SHA-256 is introduced.

## Scope

`-z`, `--boundary`, and `--objects-edge` remain deliberately deferred with `--filter-print-omitted`. Their framing and overlap semantics need dedicated modelling rather than line-oriented post-processing.

## Regression coverage

Phase254 tests cover:

- `blob:none` omitted records followed by the filtered count;
- `object:type=tree` counting with the default provided-root exemption;
- `--filter-provided-objects` removing that exemption;
- the native ordering contract for traversal, missing, and final-count records;
- retirement of the obsolete Phase253 `--count` deferral guard.
