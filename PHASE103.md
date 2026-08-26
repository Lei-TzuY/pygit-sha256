# Phase 103 — strict `commit-graph verify`

Phase 103 turns the older commit-graph cache from a best-effort binary blob into a fail-closed acceleration artifact with explicit verification.

## CLI

```bash
pygit commit-graph write
pygit commit-graph verify
```

`write` keeps the existing repository-wide graph generation workflow, but graph bytes are now serialized and parsed before installation, written through a temporary file in `.pygit/objects/info`, flushed, and atomically replaced. A successful command then re-reads the installed graph against the object database before reporting success.

`verify` validates the on-disk graph and prints a compact success summary containing the commit count and maximum generation number. Structural or repository-metadata corruption returns a non-zero status through the modern application error boundary.

## Strict format validation

The parser now rejects, rather than silently truncating or ignoring:

- bad `CGPH` signatures;
- unsupported version or chunk-count fields;
- declared commit counts that cannot fit in the file;
- truncated fixed entries or parent lists;
- duplicate or non-monotonically sorted commit IDs;
- generation zero, inconsistent generation numbers, self-parent edges, and graph cycles;
- trailing bytes after the declared graph payload.

The file format remains pygit's existing educational SHA-256 format: a 10-byte header followed by variable-length commit entries containing a 32-byte commit ID, 32-byte tree ID, generation, parent count, and 32-byte parent IDs. It is intentionally not Git's native chunked commit-graph format.

## Repository-aware verification

`CommitGraph.verify(store)` additionally proves that every graph entry still agrees with the object database:

- the indexed object exists and is a commit;
- the stored root tree exactly matches the commit object;
- the ordered parent list exactly matches the commit object;
- the referenced root tree exists and is actually a tree.

Parents outside the graph remain valid. This preserves shallow-boundary behavior: an included shallow commit may name a parent that is intentionally absent from the local graph/object closure.

## Writer hardening

`CommitGraph.write()` now validates canonical 64-character lowercase SHA-256 IDs, rejects duplicate input commits and cyclic input graphs, computes deterministic generation numbers, self-parses the complete serialized payload, and installs it with `os.replace()` only after the temporary file has been flushed and `fsync`ed. Failed writes clean up their temporary file without replacing the previous graph.

Since Phase 110, generation calculation and cycle detection use an iterative dependency traversal rather than recursive DFS. This preserves the Phase 103 binary format and shallow-parent semantics while allowing histories far deeper than Python's recursion limit to be written and verified safely.

## Regression coverage

`tests/test_phase103.py` covers round-trip verification, generation summaries, temporary-file cleanup, bad magic/version/trailing data, truncation, invalid generations, object-database metadata mismatch, cycle/duplicate rejection, modern `commit-graph write` and `verify` routing, and non-zero CLI failure on corrupted graphs. Phase 110 adds dedicated deep-history and deep-cycle regressions in `tests/test_phase110.py`.
