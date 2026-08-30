# Phase 303 — Enforce protocol-v2 textual record grammar

Phase303 adds a record-grammar trust layer on top of Phase301's exact-green HTTP-envelope and pkt-line framing hardening.

Phase301 ensures a response arrived through the expected Smart HTTP media type and has the command-specific flush envelope. Phase303 verifies that textual records inside that trusted envelope are themselves syntactically valid instead of being normalized by broad `rstrip()` behavior or silently overwritten.

## Capability advertisement grammar

Protocol-v2 capability records are parsed as `key[=value]` with Git-compatible validation:

- generic keys contain only ASCII letters, digits, `-`, and `_` and are non-empty;
- generic values are non-empty and use the protocol-v2 capability-value character set;
- syntactically valid unknown capability keys remain retained so callers continue to ignore/consume them according to forward-compatible protocol-v2 semantics;
- empty values, malformed keys, tabs, embedded LF bytes, and generic out-of-grammar punctuation are rejected;
- the documented `agent` capability keeps its wider semantic rule: any printable ASCII byte from 33 through 126 is accepted, while spaces/control bytes are rejected.

The `agent` exception is deliberate. Applying only the generic value ABNF would incorrectly reject legal configured Git user-agent strings containing printable punctuation such as `~`, `|`, quotes, backslashes, or backticks.

## Packet-line LF compatibility

Git's common pkt-line rules require receivers to tolerate a missing terminal LF even when a higher-level grammar is written as `PKT-LINE(... LF)`.

Phase303 therefore removes **at most one** terminal LF. It accepts both `record` and `record\n`, but rejects embedded LF or multiple trailing LF bytes. This avoids the old `rstrip(b"\n")` behavior, which could silently convert malformed records into valid-looking ones.

The rule is applied to:

- the Smart HTTP service marker;
- the `version 2` record;
- capability records;
- `ls-refs` records.

## `ls-refs` structural validation

The existing native 40-hex SHA-1 validation remains in place and record handling is hardened further:

- repeated separators / empty fields are rejected;
- NUL bytes are rejected;
- duplicate ref-name records are rejected instead of last-write-wins overwriting;
- duplicate `symref-target` and duplicate `peeled` attributes are rejected;
- empty `symref-target` values are rejected;
- peeled identities remain required to be full native 40-hex SHA-1 OIDs;
- unknown attributes remain ignored for forward compatibility rather than becoming repository-visible state.

## Git / SHA-256 invariants

No object identity semantics change.

- protocol transport identities remain genuine remote-native full 40-hex SHA-1 OIDs;
- repository-visible objects remain content-derived local SHA-256;
- no SHA-1 padding, truncation, translation, surrogate SHA-256, or metadata-only native-to-local mapping is introduced;
- no new content materialization path is added.

## Regression coverage

`tests/test_phase303.py` covers:

1. missing terminal LF compatibility for version, capability, and `ls-refs` records;
2. valid unknown capability retention;
3. generic capability key/value ABNF rejection cases;
4. extended printable `agent` values, including a native Git `GIT_USER_AGENT` stateless-rpc probe;
5. embedded/multiple LF rejection;
6. repeated-field, NUL, duplicate-ref, duplicate-symref, empty-symref, and duplicate-peeled rejection;
7. preservation of unknown `ls-refs` attribute compatibility.

## Coordination

- actual `main` at phase start: `bfcbae64e4dc9997b915c16e1aa923a951090083`;
- base: Phase301 / PR #279 exact-green head `f2f1eb0c2426dafa6de8f655f45870bdfd64689d`;
- Phase301 Tests #2642: Python 3.9 / 3.13 both 2245 passed, CI Git 2.55.0;
- Phase303, Phase304, and Phase305 were checked before branch creation and were free;
- Phase303 does not modify the open Phase300 / PR #276 stack base.

The phase is complete only after its own full Python 3.9 / 3.13 GitHub Actions matrix passes on the exact final head.
