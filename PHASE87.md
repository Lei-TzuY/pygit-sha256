# Phase 87 — nested annotated-tag `for-each-ref --points-at` compatibility

Phase 87 hardens the Phase 85 `for-each-ref --points-at` implementation to match native Git when refs point through multiple annotated-tag objects.

## Problem

A nested tag chain can look like:

```text
refs/tags/outer -> tag outer -> tag inner -> commit C
refs/tags/inner ------------^             
```

Native Git treats every object in that peel chain as an object the ref points at. Therefore both of these queries must include `refs/tags/outer`:

```bash
pygit for-each-ref --points-at=<inner-tag-object>
pygit for-each-ref --points-at=<commit-C>
```

Phase 85 compared only the ref's stored OID and its final recursively peeled OID. That handled ordinary annotated tags but skipped intermediate tag objects in nested chains.

## Fix

`pygit.ref_query` now walks the complete annotated-tag chain for each candidate ref and matches `--points-at` targets against every encountered object ID:

```text
direct ref OID -> intermediate tag OID(s) -> final peeled target
```

Existing tag-cycle detection remains fail-closed, and the public `RefRecord` shape is unchanged.

## Scope

This is a read-only compatibility correction. It does not change ref storage, tag serialization, revision parsing, sorting, graph predicates, count limiting, the index, or the worktree.

## Regression coverage

`tests/test_phase87.py` covers:

- matching an intermediate annotated-tag object in a two-level tag chain;
- matching the final commit through multiple annotated tags;
- the installed `pygit for-each-ref` CLI path.
