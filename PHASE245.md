# Phase245: ordinary `rev-list -z` object metadata

Phase245 extends the Phase244 NUL-delimited object protocol to ordinary SHA-256 repositories, without requiring a `--missing` mode.

## Supported forms

- `pygit rev-list --objects -z <revisions>`
- `pygit rev-list --objects --no-object-names -z <revisions>`
- `pygit rev-list --objects --boundary -z <revisions>`

Records follow Git's current documented metadata framing: an object identity followed by NUL-delimited `token=value` fields. Present identities are genuine repository SHA-256 values. Paths are emitted verbatim as `path=...`, including embedded newlines. Boundary commits use `boundary=yes` instead of a textual `-` prefix.

## Missing-object integrity

Ordinary `-z` does not silently weaken integrity. If the metadata inventory encounters an unresolved promise without an explicit `--missing` policy, traversal fails and tells the caller to select `allow-promisor`, `print`, or `print-info`. Foreign SHA-1 identities therefore never leak into ordinary repository-visible records.

## Deliberate limits

`--objects-edge` and `--count` remain rejected under `-z` until their documented machine-readable framing is implemented explicitly.

## Verification

Tests cover ordinary SHA-256 identities, verbatim newline-containing paths, `--no-object-names`, and rejection of incompatible count/object-edge combinations. Phase244 partial-clone tests continue to cover `missing=yes`, `boundary=yes`, zero demand-fetches, and SHA-domain separation.
