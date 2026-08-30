# Phase 269 — ordered blob-limit omissions with structured `-z`

Phase269 composes the current SHA-256-native ordered `rev-list` stack with Git's
modern structured NUL object protocol for the already-supported
`blob:limit=<n>[kmg] + --filter-print-omitted` combination.

## Supported composition

```text
pygit rev-list --objects --in-commit-order -z \
  --filter=blob:limit=<n>[kmg] --filter-print-omitted [--boundary] <revisions>
```

The ordered inventory remains authoritative. Phase269 does not introduce a new
object walker and does not reinterpret object identities.

## Git 2.55 framing contract

Git 2.55's `builtin/rev-list.c` sets both `line_term` and `info_term` to NUL when
`-z` is present. Normal objects and explicit missing-object records therefore use
the structured protocol:

```text
<OID> NUL [token=value NUL]...
```

The omitted-object loop is intentionally different: it still calls the
hard-coded newline form `printf("~%s\n", ...)`. Missing-object records are emitted
after that omission loop. Phase269 therefore preserves the observable mixed
stream already established by Phase257:

```text
NUL traversal records -> newline ~<local-sha256> omissions -> NUL missing records
```

No `omitted=yes` token is invented.

`--boundary` stays structured: a boundary commit is represented as
`<local-sha256>\0boundary=yes\0` rather than a textual `-<oid>` line.

The official `git-rev-list` documentation describes `-z` as compatible with the
`--objects`, `--boundary`, and `--missing` output options. Phase269 therefore keeps
the existing `--objects-edge` rejection. The existing project-level `-z +
--count` guard is also left unchanged in this focused phase; reconciling the
current documentation wording with the exact native count behavior is reserved
for a dedicated compatibility phase rather than silently widening this change.

## SHA-256-native boundary

Every repository-visible present or omitted object remains a genuine local
64-hex SHA-256 identity. The `~` channel never accepts a native/upstream SHA-1.

`blob:limit` still requires the uncompressed size of every candidate blob.
Persistent promisor metadata currently records native identity and type, but not
blob size. An unresolved promised blob is therefore rejected before any output
instead of being demand-fetched merely to classify the filter, guessed, padded,
translated, or represented by a surrogate SHA-256.

## Coordination

- `main`: `bfcbae64e4dc9997b915c16e1aa923a951090083`
- base: Phase268 clean PR #246 exact green head
  `1c8594c4528964ef017680f20bf94a2b616c3b8b`
- Phase269 and Phase270 branches were absent before work began
- the earlier collided Phase268 branch remains untouched; Phase269 is stacked on
  the clean replacement only

## Verification scope

Phase269 regressions cover:

- exact mixed NUL/newline framing for ordered `blob:limit` omissions
- preservation of `path=` metadata for surviving objects
- structured `boundary=yes` metadata before omissions
- no invented `omitted=yes` token
- retained `-z + --objects-edge` and project-level `-z + --count` guards
- plain ordered `blob:limit -z` remaining a separate deferred composition
- a native SHA-256 Git 2.55 byte-level probe for normal and boundary framing

The full Python 3.9/3.13 GitHub Actions matrix remains the authoritative suite
gate. The PR intentionally stays open and unmerged.
