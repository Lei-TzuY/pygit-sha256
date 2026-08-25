# Phase 58 — typed `hash-object`

Phase 58 closes the remaining low-level object-ingestion gap after the Phase 55/57 object inspection and revision work.

Implemented:

- SHA-256 hashing for exact native object envelopes
- `blob`, `tree`, `commit`, and `tag` payload types
- multiple positional files
- raw `--stdin`
- newline-delimited `--stdin-paths`
- idempotent `-w` loose-object storage
- structured payload validation before hashing/writing
- Python helpers for bytes and filesystem paths
- installed CLI routing for both `pygit` and `python -m pygit`

Regression coverage lives in `tests/test_phase58.py`. The phase keeps filters, `--path`, and `--literally` out of scope because they require different semantics than native object ingestion.
