# Phase 99 — `update-ref --stdin -z` NUL framing

Phase 99 adds Git-style NUL-delimited framing for direct-reference `update-ref --stdin` transactions. The transaction engine itself remains the Phase 98 implementation; this phase adds a byte-oriented protocol layer so machine-generated input does not depend on line splitting or quoting.

## CLI

```bash
printf 'create refs/heads/topic\0<oid>\0' | pygit update-ref --stdin -z
printf 'start\0update refs/heads/topic\0<new>\0<old>\0prepare\0commit\0' \
  | pygit update-ref --stdin -z -m 'batch update'
```

`-z` is valid only with `--stdin`. Direct-ref commands follow Git's NUL protocol:

```text
update SP <ref> NUL <new-oid> NUL <old-oid-or-empty> NUL
create SP <ref> NUL <new-oid> NUL
delete SP <ref> NUL <old-oid-or-empty> NUL
verify SP <ref> NUL <old-oid-or-empty> NUL
option SP no-deref NUL
start NUL
prepare NUL
commit NUL
abort NUL
```

An empty optional old-value field means the value was not supplied. The field itself must still be present; truncated NUL streams are rejected instead of being silently reinterpreted. For `update`, an empty required new-value field is normalized to the all-zero object ID, matching Git's delete interpretation. `create` still requires a non-empty new object value.

## Architecture

`pygit.update_ref_protocol.parse_update_records_z()` owns byte framing and returns the existing `RefUpdate` records. It deliberately does not perform object resolution, compare-and-swap checks, transaction state changes, or publication. Those remain centralized in `pygit.ref_transaction.update_refs()`.

This separation keeps NUL bytes out of the line-mode parser and avoids duplicating Phase 98 transaction semantics. `--no-deref`, one-shot `option no-deref`, explicit `start` / `prepare` / `commit` / `abort`, reflog messages, atomic validation, zero-OID deletion, and EOF abort behavior therefore compose unchanged.

## Scope boundary

Current Git also defines `symref-update`, `symref-create`, `symref-delete`, and `symref-verify` inside `update-ref --stdin`. Phase 99 does not approximate those commands; symbolic-ref transaction verbs remain separate future work. The existing standalone `symbolic-ref` plumbing is unchanged.

The parser decodes protocol fields as UTF-8 because pygit's ref backend is path/string based. Invalid UTF-8 fields are rejected explicitly.

## Regression coverage

`tests/test_phase99.py` covers parser field mapping, empty optional values, empty update-new zero normalization, malformed/truncated streams, invalid UTF-8, empty input, explicit prepare/commit, explicit EOF abort, one-shot `no-deref`, zero-OID deletion, CAS atomicity, CLI option validation, and installed CLI help.
