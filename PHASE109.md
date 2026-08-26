# Phase 109 — complete `commit-graph write` reachability

Phase 109 fixes the root-selection layer behind the strict Phase 103 commit-graph writer. The binary graph format and verifier are unchanged; this phase makes the installed writer actually cover the repository reachability it advertises.

## Problem

The older installed write path delegated to `Repository.write_commit_graph()`, which populated the graph from `log(all_branches=True)`. That history walk seeded only local branch tips, falling back to `HEAD` only when no local branch existed. As a result, a repository-wide CLI write could omit commits reachable solely from:

- remote-tracking refs;
- lightweight or annotated tags;
- refs stored only in `packed-refs`;
- a detached `HEAD` whenever at least one local branch also existed.

Those omissions are especially confusing for an acceleration artifact: the object database and revision plumbing could reach the commits, while a freshly written commit-graph silently could not describe them.

## Repository-wide selection

The modern `commit-graph write` command now composes the existing `rev-list` traversal with the Phase 103 codec. A default installed-command write uses every commit-ish ref plus `HEAD` as roots:

```bash
pygit commit-graph write
pygit commit-graph verify
```

`rev-list` supplies the graph walk, so tag peeling, packed refs, remote refs, deterministic traversal, and `.pygit/shallow` boundaries share the same semantics as the repository's existing revision plumbing. `HEAD` is added independently of the ref set, preserving detached-head reachability.

An empty repository remains valid and produces an empty graph with commit count and maximum generation both zero.

Phase 113 adds an optional read-only coverage check for this exact root set:

```bash
pygit commit-graph verify --reachable
```

Plain `verify` intentionally remains structural/repository-aware only so deliberately partial graphs stay valid.

## Explicit roots from stdin

For scripts that need a deliberately smaller graph, Phase 109 adds:

```bash
printf '%s\n' main topic | pygit commit-graph write --stdin-commits
```

Each non-empty input line is a commit-ish root. Only those roots and their reachable commit ancestry are written; unrelated repository refs are not implicitly added. Annotated tags are peeled through the shared revision resolver.

Blank-only stdin is rejected rather than falling back to repository-wide mode. Unknown or non-commit roots fail before `CommitGraph.write()` runs, so an already-installed valid graph is not replaced by a failed explicit selection.

Phase 113 can verify coverage of the same explicit-root semantics through `commit-graph verify --stdin-commits`.

## Compatibility and safety boundary

The on-disk file remains pygit's educational SHA-256 `CGPH` format, not Git's native commit-graph format. Phase 109 changes the modern installed-command selection layer; the older high-level `Repository.write_commit_graph()` method is retained for compatibility and is not silently given new root-selection semantics in this phase.

The Phase 103 write guarantees still apply after selection: canonical IDs, cycle/generation validation, self-parse, temporary-file write, `fsync`, atomic replacement, and final repository-aware verification.

## Regression coverage

`tests/test_phase109.py` covers packed branch/remote/tag roots, annotated-tag peeling, detached `HEAD`, shallow explicit traversal, explicit subset isolation, empty repositories, missing and non-commit input without graph replacement, stdin CLI selection, blank stdin rejection, and help output.
