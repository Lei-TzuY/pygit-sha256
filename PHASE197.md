# Phase 197 — Fetch negotiation controls

Phase197 adds explicit control over the commit set pygit reports to a smart-HTTP
server during fetch negotiation.

## CLI

```text
pygit fetch --negotiation-restrict=<commit-or-glob> origin
pygit fetch --negotiation-tip=<commit-or-glob> origin
pygit fetch --negotiation-include=<commit-or-glob> origin
```

`--negotiation-restrict` is the preferred current Git spelling.
`--negotiation-tip` remains an accepted synonym for compatibility with Git
versions that exposed that name first. Both options may be repeated and their
resolved tip sets are combined.

`--negotiation-include` may also be repeated. Restriction first replaces the
normal broad have set with commits reachable from the selected tips; include
then guarantees that the exact selected tip commits are added to the have set.

Options are recognized only before the standard `--` option terminator. Tokens
after `--` remain ordinary fetch refspec text.

## Revision and glob resolution

Exact values may name local branches, tags, remote-tracking refs, `HEAD`, or a
unique SHA-256 object prefix. Annotated tags are peeled to commits. Glob values
match full local ref names such as `refs/heads/release/*`.

A missing value, unmatched glob, missing revision, or non-commit target is a
hard error before contacting a remote.

Commit ancestry walks respect `.pygit/shallow`: a shallow-boundary commit is
reported as a possible have, but its parents are not traversed beyond the local
history boundary.

## Native transport boundary

pygit stores objects under SHA-256 identities, while the interoperable
protocol-v0 server expects SHA-1 object names in `have` lines. Phase197 resolves
and walks negotiation tips entirely in the local SHA-256 object graph, then
uses the existing `NativeExporter` plus known per-remote native maps to obtain
the corresponding native SHA-1 commit IDs.

This changes negotiation only. It does not rewrite refs, object storage, pack
formats, or FETCH_HEAD semantics.

## Refetch composition

Current Git accepts negotiation controls together with `--refetch`, but
`--refetch` is defined to fetch as a fresh clone rather than avoiding objects by
normal have negotiation. pygit therefore still validates every requested
negotiation tip, then lets Phase196's empty-have refetch policy win for the
actual transfer.

## Compatibility boundary

Current Git 2.54 documentation also defines `--negotiate-only`, which performs a
negotiation-only exchange and prints common ancestors without fetching or
updating refs. That requires an ACK-only protocol path rather than a have-set
planner and is intentionally reserved for a subsequent phase.

Likewise, `remote.<name>.negotiationInclude` is not claimed in this phase; the
Phase197 scope is explicit command-line negotiation policy.

## Tests

`tests/test_phase197.py` covers:

- preferred and legacy option spellings;
- option-terminator handling and missing values;
- exact revision and full-ref glob resolution;
- shallow-aware ancestry;
- native SHA-1 planning for reachable and exact included commits;
- restricted replacement and included augmentation of existing haves;
- transport-scope restoration after normal and exceptional exits;
- CLI stripping before the established fetch parser; and
- validation plus refetch precedence when both policies are requested.
