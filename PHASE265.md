# Phase 265 — `rev-list --in-commit-order --filter-print-omitted`

Phase265 composes the metadata-only ordered traversal from Phases259-264 with
Git's independent filter-omission presentation channel.

## User-visible behavior

The following combinations are now supported for `blob:none`:

```text
pygit rev-list --objects --in-commit-order \
  --filter=blob:none --filter-print-omitted HEAD

pygit rev-list --objects --in-commit-order --reverse \
  --filter=blob:none --filter-print-omitted HEAD

pygit rev-list --objects --in-commit-order --boundary \
  --filter=blob:none --filter-print-omitted HEAD

pygit rev-list --objects-edge --in-commit-order \
  --filter=blob:none --filter-print-omitted A..B

pygit rev-list --objects --in-commit-order --count \
  --filter=blob:none --filter-print-omitted HEAD

pygit rev-list --objects --in-commit-order -z \
  --filter=blob:none --filter-print-omitted HEAD
```

The line-oriented output order is:

```text
ordered traversal / edges / boundaries
~omitted-local-sha256
?missing-native-identity
final count
```

`--count` suppresses ordinary present-object records exactly as before, while
omission records remain visible and do not contribute to the filtered count.

## Composition instead of another walker

Phase264 already applies `blob:none` to `PromisorObjectInventoryEntry` values in
the commit/snapshot-interleaved traversal. Phase265 does not duplicate that
walk. A small adapter captures Phase264's output and reuses the mature
Phase253-257 omission helpers to compute and validate the omission set.

This keeps three responsibilities separate:

1. Phase259-264 own ordered object traversal and filtering.
2. Phase253-257 own Git-compatible omitted-object collection and SHA-domain
   validation.
3. Phase265 owns only presentation composition and ordering between those
   channels.

Explicit negative revision closure is therefore still authoritative. For
`base..tip --objects-edge`, a blob reachable only from the excluded base closure
is neither selected nor reported as omitted.

## Git compatibility

Git 2.55 documents:

- `--in-commit-order` prints tree/blob ids after the first commit that references
  them;
- `--filter=<filter-spec>` is useful with `--objects*` and `blob:none` omits all
  blobs;
- `--filter-print-omitted` prints objects omitted by the filter with a leading
  `~`.

A deterministic native SHA-256 Git 2.47.3 fixture was also probed before the
implementation. Its line-oriented behavior confirms:

- normal and reverse ordered traversal completes before the `~` omission list;
- `--count` emits omissions before the final filtered count;
- boundary traversal completes before omissions;
- omission-list ordering itself is not treated as a stable traversal-order API,
  so tests validate set membership while asserting the channel boundaries
  exactly.

For `-z`, Phase265 follows the already-established Phase257/current-Git
contract: normal object records use the structured NUL protocol, while the
omission loop remains the legacy newline-framed `~<oid>\n` channel. Phase265
therefore does not invent an `omitted=yes` token.

## SHA-256-native boundary

The omission channel is a repository object-id channel. Every emitted omitted
object must therefore have its genuine local 64-hex SHA-256 identity.

An unresolved promised blob has only its upstream/native SHA-1 until its content
is materialized. If `--filter-print-omitted` would need to report such a blob,
Phase265 fails before emitting any captured traversal output. It never pads,
translates, or substitutes the 40-hex native SHA-1 as a local SHA-256.

The failure path remains metadata-only: no single-object fetch or batch fetch is
triggered merely to manufacture an omission identity, and promisor state remains
unchanged.

## Deliberate scope

Phase265 supports `blob:none`, the filter already integrated into ordered
traversal by Phase264. `object:type`, `blob:limit`, disk-usage composition, and
additional filter families remain separate follow-up phases so their native
omission semantics can be modeled independently.

`-z + --objects-edge` and `-z + --count` remain rejected by the existing output
compatibility guards.

## Verification

Focused regression coverage includes:

- normal ordered traversal followed by local SHA-256 omissions;
- reverse ordered traversal with omissions still after traversal;
- omitted-before-count framing;
- boundary traversal before omissions;
- object-edge exclusion closure and base-only blob suppression;
- Phase257-compatible mixed NUL/newline framing;
- unresolved-promisor refusal with zero fetches, no output, and unchanged state;
- explicit rejection of still-unmodelled filter families.

The full repository suite is run by the existing GitHub Actions Python 3.9 and
3.13 matrix on the exact PR head.
