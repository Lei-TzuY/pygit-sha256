# Phase 92 — `for-each-ref --stdin` blank-record hardening

Phase 92 corrects one native-Git compatibility edge in the Phase 90 stdin pattern adapter.

Git distinguishes true EOF from blank pattern records:

- empty stdin supplies zero patterns, so `for-each-ref --stdin` selects all refs;
- one or more blank records supply empty patterns, which match no refs unless another non-empty pattern is also present.

The shared `read_ref_patterns()` helper continues to normalize away blank records for callers that already track source-record presence. The installed CLI now preserves whether stdin produced any records and injects an empty-pattern sentinel only when the source contained records but normalization yielded no patterns.

This change is read-only and does not affect exclusions, object/graph filters, sorting, formatting, packed refs, or repository state.

Regression coverage in `tests/test_phase92.py` locks both empty-stdin and blank-only behavior against the installed CLI.
