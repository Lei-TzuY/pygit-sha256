# Phase 340 — incremental named-remote FETCH_HEAD publication

Phase 340 extends the exact-green Phase 339 incremental protocol-v2 packfile-URI path with Git-compatible `FETCH_HEAD` publication.

## Native Git behavior used as the contract

Probes were run against native Git with SHA-256 repositories.

- A successful `git fetch origin main` writes the fetched repository-native SHA-256 object id to `FETCH_HEAD`.
- Repeating an already-up-to-date fetch still replaces stale `FETCH_HEAD` content with the current fetched tip.
- `git fetch origin` through the configured refspec marks fetched branch records `not-for-merge`.
- Explicit command-line branch selection such as `git fetch origin main feature` uses the empty/mergeable marker for the selected refs.
- If a requested remote branch does not exist, the old `FETCH_HEAD` is truncated rather than left stale.
- If object transfer succeeds but a later remote-tracking ref update cannot acquire its lock, native Git still keeps the newly fetched tip in `FETCH_HEAD` while the local tracking ref remains unchanged.

The final point defines the ordering boundary: `FETCH_HEAD` describes verified fetched tips, not merely successfully updated remote-tracking refs.

## Implementation

`fetch_named_remote_incrementally_with_packfile_uris()` now:

1. preserves the existing mutation-free `None` fallback when initial discovery is not protocol v2;
2. truncates `FETCH_HEAD` after successful v2 discovery, before branch selection and later transport/publication failures;
3. performs the existing mapped incremental negotiation, staging, optional immutable LMAP publication, and root certification;
4. projects each selected local `refs/remotes/<remote>/...` publication back to its source `refs/heads/...` name;
5. writes only the **certified local 64-hex SHA-256 root** to `FETCH_HEAD` — never a transport SHA-1 id;
6. emits that metadata after certification and before tracking-ref CAS publication;
7. marks explicit `branches=...` selections mergeable and default/refspec-style selections `not-for-merge`.

The lower-level incremental transaction gains a narrow optional post-certification / pre-ref-publication hook. It is deliberately executed only after the root certificate has re-read, re-hashed, and type-checked the local objects. A hook failure aborts before any tracking-ref publication.

## SHA-256 identity boundary

Transport-native object identities remain genuine 40-hex SHA-1 values and are used only to bind advertisements, negotiation, LMAP mappings, and root certificates.

`FETCH_HEAD` is repository-native metadata, so Phase 340 takes its OIDs exclusively from `PackfileUriRootCertificate.native_to_local`. No SHA-1 padding, truncation, surrogate IDs, or synthetic translation is introduced.

## Tests

`tests/test_phase340.py` covers:

- projection from local remote-tracking ref names back to source branch names;
- certified local SHA-256 identity in `FETCH_HEAD`;
- explicit-selection mergeable markers;
- default/refspec `not-for-merge` markers;
- already-up-to-date known-only fetch replacing stale metadata;
- stale-file truncation when selected branch planning fails;
- post-certification `FETCH_HEAD` persistence when tracking-ref publication later fails;
- mutation-free protocol-v2 discovery fallback;
- hook type validation;
- native SHA-256 Git marker, truncation, and ref-lock failure ordering probes.

## Deferred

This phase remains branch-only because the Phase 328 tracking planner is branch-only. Annotated/lightweight tag publication and any corresponding tag `FETCH_HEAD` semantics should be added only when the underlying named-remote tag transaction has a verified object-type design.
