# Phase 110 — recursion-safe commit-graph generations

Phase 110 removes a scale-dependent correctness limit from the strict commit-graph codec introduced in Phase 103 and fed by the broader reachability selection from Phase 109.

## Problem

Generation numbers were previously computed with a recursive depth-first walk. That was compact, but a perfectly valid linear history deeper than Python's recursion limit could fail with `RecursionError` while writing or parsing a commit-graph. The failure was unrelated to repository corruption and became more likely precisely where a commit-graph acceleration file is most useful.

The same recursive routine also performed cycle detection, so very deep cyclic input could hit the interpreter recursion limit before reporting the intended `CommitGraphError`.

## Iterative generation engine

`CommitGraph._compute_generations()` now uses an iterative Kahn-style dependency traversal over included parent edges. It maintains, per commit:

- the number of included parents not processed yet;
- reverse parent-to-child edges;
- the maximum generation seen from processed parents.

Ready commits are processed from a heap for deterministic behavior. Once all included parents of a commit are complete, its generation is finalized and its children are released.

This makes generation calculation O(V + E) apart from deterministic heap operations and, importantly, independent of Python call-stack depth.

## Compatibility semantics

The on-disk `CGPH` format is unchanged. Existing generation semantics are preserved:

- a root commit has generation 1;
- a parent outside the graph contributes generation 1, preserving shallow/external-boundary behavior;
- duplicate parent entries remain legal input and do not stall traversal;
- cycles still fail closed with `CommitGraphError`;
- generations are explicitly bounded to the format's 32-bit field.

Both serialization and strict parsing use the same iterative engine, so deep histories are safe on write, self-parse, read, and verification paths.

## Regression coverage

`tests/test_phase110.py` covers:

- serialization and parsing of a 5,000-commit linear history;
- public atomic write/read of a 3,000-commit history;
- a 3,000-node cycle reporting `CommitGraphError` rather than `RecursionError`;
- preserved external-parent generation semantics;
- duplicate parent-edge handling.
