# Phase211 — compose `fetch --update-shallow` with explicit shallow controls

Phase211 removes an intentionally conservative Phase210 incompatibility and aligns pygit more closely with native Git: `--update-shallow` can now accompany an explicit shallow-history mutation such as `--depth`, `--deepen`, `--unshallow`, `--shallow-since`, or `--shallow-exclude`.

## Why this is safe

`--update-shallow` is a safety opt-in for fetches from a shallow source repository. Git normally refuses refs that would require changing the local shallow boundary; with `--update-shallow`, it updates `.git/shallow` and accepts them.

The explicit shallow-history options already request that same class of boundary mutation. pygit's Phase202/208 transports already own the complete transaction for those requests:

- protocol-v2 shallow/deepen/selectors on the wire;
- stable foreign-parent importing for genuinely truncated native packs;
- native SHA-1 ↔ repository SHA-256 boundary translation;
- application of returned `shallow-info` to `.pygit/shallow`.

Therefore Phase211 treats an additional `--update-shallow` as redundant when one of those explicit mutations is active. It strips only `--update-shallow` and delegates to the proven shallow transport instead of nesting a second importer/boundary-update scope.

Standalone `--update-shallow` still uses Phase210 unchanged. Ordinary fetches without it still retain Phase210's warning-only shallow-source refusal guard.

## Git compatibility

Current `git-fetch` documentation defines:

- `--deepen=<depth>` as changing history relative to the current shallow boundary;
- `--shallow-since=<date>` and repeatable `--shallow-exclude=<ref>` as deepening or shortening shallow history;
- `--unshallow` as removing or extending shallow limitations depending on the source;
- `--update-shallow` as allowing `.git/shallow` to be updated when fetching from a shallow source.

A local native Git 2.47.3 probe confirmed that `git fetch --update-shallow --deepen=1 origin` and `git fetch --update-shallow --shallow-since=<date> origin` are accepted. `--update-shallow --unshallow` is likewise syntactically accepted; a later probe on an already-complete repository failed only because `--unshallow` itself no longer made sense after the previous deepen/since operations.

## Parsing boundary

Phase211 preserves two important command-line boundaries:

1. options after the standard `--` terminator remain literal refspec arguments;
2. protocol-v2 server-option payloads are removed before shallow-option detection, so `-o --deepen=2` is not accidentally reinterpreted as a client-side `--deepen` request.

## SHA-256-native design

No object identity changes are introduced. Repository-visible objects, refs, `FETCH_HEAD`, foreign-commit records, and `.pygit/shallow` remain SHA-256-native. Native Git SHA-1 remains confined to smart-HTTP negotiation, pack import/export, preserved foreign-parent metadata, and native-map translation.

## Scope

This phase deliberately does not broaden the existing multi-remote, prefetch, refetch, or negotiate-only compatibility matrix. It only removes the redundant `--update-shallow` conflict for shallow-history mutations that already have a dedicated protocol-v2 transaction.
