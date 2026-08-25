# Phase 88 — packed nested-tag query integration

Phase 88 adds a focused cross-phase regression between Phase 86 hardened pack reads and Phase 87 nested annotated-tag `for-each-ref --points-at` matching.

The test constructs `outer -> inner -> commit`, repacks all objects so subsequent reads use the pack path, and verifies that queries for both the intermediate tag object and the final commit still match `inner` and `outer`.

This phase intentionally changes no production code. Its purpose is to lock the interaction between strict `PackReader` validation and full annotated-tag peel-chain matching on the actual integrated `main` state.
