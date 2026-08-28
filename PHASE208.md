# Phase 208 — protocol-v2 shallow date/ref selectors

Phase208 extends the reconciled Phase207 fetch stack with Git-style shallow-history selectors while preserving pygit's SHA-256-native repository identity.

## Added fetch controls

- `--shallow-since=<date>` / `--shallow-since <date>`
- repeated `--shallow-exclude=<ref>` / `--shallow-exclude <ref>`

`--shallow-since` is translated to protocol-v2 `deepen-since <timestamp>`. `--shallow-exclude` is translated, in command-line order, to repeated `deepen-not <rev>` lines. The implementation accepts Unix epoch timestamps and ISO-8601 dates/times; protocol-v2 receives the required integer timestamp.

The selectors operate on an existing shallow repository, reuse the current local `.pygit/shallow` boundary, translate those SHA-256 boundary IDs to the owning remote's native SHA-1 IDs, and run through the stable shallow importer. Any returned `shallow-info` is translated back to repository-visible SHA-256 IDs.

## Compatibility and composition

Git protocol v2 defines `deepen-since` and `deepen-not` under the advertised `fetch=shallow` feature. `deepen-not` may be repeated and may be combined with `deepen-since`, but neither selector form is combined here with depth/deepen/unshallow controls. The first implementation also keeps the existing Phase202 single-named-remote boundary and rejects multi-source/prefetch/refetch/negotiate-only and explicit negotiation restriction combinations rather than inventing ambiguous semantics.

The Phase207 server-option reconciliation remains intact. Explicit or configured server options and shallow selectors are emitted in the same protocol-v2 exchange, with server options retained in the capability-list and selector lines in the fetch argument section. A v0 fallback is rejected whenever selectors are active.

The standard `--` option terminator is preserved: tokens such as `--shallow-since=...` after `--` remain literal refspec arguments.

## SHA-256-native design

Repository-visible commits, refs and `.pygit/shallow` continue to use 64-hex SHA-256 IDs. Native SHA-1 remains confined to smart-HTTP interoperability, native maps, preserved foreign-parent metadata and native object export/import boundaries. Shallow date/ref selectors do not change local object identity.

## Protocol grounding

Current Git fetch documentation defines `--shallow-since=<date>` as deepening or shortening a shallow repository to include reachable commits after the selected date, and `--shallow-exclude=<ref>` as excluding commits reachable from a specified remote branch or tag; the exclude option may be repeated.

Current Git protocol-v2 documentation defines the corresponding wire arguments as `deepen-since <timestamp>` and `deepen-not <rev>`. `deepen-not` may be combined with `deepen-since`, while both are alternatives to `deepen`.

## Verification target

- base: Phase207 / PR #184 exact head `36c430150af49dbc0ab64e3138df950b8191fb86`
- Phase207 GitHub Actions Tests #1858: success on Python 3.9 / 3.13 before Phase208 branch creation
- focused Phase208 regressions cover date parsing, repeat ordering, option termination, protocol framing, server-option composition, shallow-feature enforcement, SHA-256↔native boundary translation, existing-shallow enforcement and CLI forwarding
- full Python 3.9 / 3.13 GitHub Actions matrix remains the final gate
