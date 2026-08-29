# Phase230 — Promisor-aware buffered cat-file batching

Phase230 removes partial-clone one-object-at-a-time demand fetching from
`cat-file --batch-command --buffer` when one flush group names multiple foreign
`REV:path` blobs.

## Git compatibility

Git defines `flush` in buffered `--batch-command` mode as the execution boundary
for all commands issued since the previous flush.  Phase230 uses that same
boundary as the prefetch unit: it predicts unresolved final blob promises for the
queued group, materializes the deduplicated set once, and then delegates parsing,
resolution, formatting, object reads, missing-record behavior, and output framing
to the historical cat-file implementation.

Non-buffered batch-command execution remains unchanged and interactive.

## Demand planning

The planner handles ordinary `REV:path` expressions and blob-compatible peel
selectors such as `^{blob}` without reading the unresolved final `TreeEntry.sha`.
It resolves only commit/tag/tree metadata and walks locally retained tree objects.
For `blob:none` repositories this allows several final native SHA-1 promises to
be collected before any one of them triggers lazy materialization.

The planner deliberately declines to prefetch when:

- the expression is not a tree path
- it is an index `:path` expression
- the path is malformed or missing
- an explicit peel selector cannot succeed for a blob
- an intermediate tree itself is unresolved

Those cases remain authoritative in the existing revision/cat-file resolver, so
Phase230 itself adds no speculative prefetch for them.  The historical resolver
may still perform its established lazy materialization while resolving the base
object before a later peel/type check; Phase230 intentionally does not redefine
that resolver ordering.

## Flush boundaries

Promises are never pulled forward across a later `flush` group.  A first group
containing one promised blob retains the established Phase213 single-object fetch
path; a later group containing two promised blobs uses the Phase214 bulk path.
Duplicate expressions inside one group are deduplicated before materialization.

## SHA-256-native identity

Native Git SHA-1 remains confined to foreign tree entries, promisor metadata, and
protocol requests.  Materialized blobs are imported under their real
content-derived SHA-256 object IDs.  Phase230 does not alter tree serialization,
refs, the index, pack handling, or object identity.

## Compatibility with the promisor stack

The implementation composes with:

- Phase213 single-object lazy materialization
- Phase214 multi-object fetch batching
- Phase221 multi-promisor fallback and shrinking wants
- Phase222 `extensions.partialClone` primary-promisor-last ordering
- per-remote `serverOption`

## Verification

Focused tests cover:

- two `REV:path` requests in one flush becoming one bulk request
- exact `serverOption` forwarding
- unrequested promises remaining unresolved
- separate flush groups preserving their demand boundary
- Phase213 single-object behavior for a one-object group
- duplicate-expression deduplication
- incompatible blob peel selectors being excluded from Phase230 prefetch while
  preserving the historical resolver's lazy-fetch/type-check ordering
- ordinary non-buffered cat-file remaining on the historical path

The complete existing test matrix must remain green on Python 3.9 and 3.13.
