# Phase 194 — fetch quiet / verbose output controls

Phase194 extends the modern fetch porcelain with Git-style output controls while preserving the SHA-256-native repository model and all Phase181–193 fetch semantics.

## Added

- `pygit fetch -q` / `pygit fetch --quiet`
  - suppresses successful single-remote summary output;
  - suppresses per-remote progress lines for `--multiple`, `--all`, remote groups, and `fetch.all=true`;
  - does **not** suppress fetch failures on stderr.
- `pygit fetch -v` / `pygit fetch --verbose`
  - keeps the normal successful summary;
  - emits deterministic fetched-ref diagnostics, including refs that were already present/up to date;
  - works for both single-source and multi-source fetches.
- Repeated `-q` / `-v` options use command-line order: the last one wins, matching native Git behavior.

## Git compatibility

The upstream `git-fetch` documentation defines `-q/--quiet` as suppressing fetch progress/internal command output, while `-v/--verbose` enables verbose status reporting. Git documents that up-to-date refs are shown only in verbose mode.

A native Git 2.47.3 local probe also confirms option-order behavior:

- `git fetch -q -v ...` is verbose;
- `git fetch -v -q ...` is quiet.

Phase194 follows those observable porcelain rules. pygit's status text remains intentionally pygit-native rather than claiming byte-for-byte compatibility with Git's transport-specific status table.

## SHA-256-native design

This phase changes presentation only. Object IDs stored by pygit remain SHA-256, local refs and `FETCH_HEAD` remain SHA-256-native, and the existing native SHA-1 smart-HTTP interoperability boundary is unchanged.

## Verification

Focused tests cover long and short quiet/verbose options, deterministic verbose ref ordering, last-option-wins behavior, and multi-remote output suppression/detail propagation. The full Python 3.9 / 3.13 suite remains the final gate.
