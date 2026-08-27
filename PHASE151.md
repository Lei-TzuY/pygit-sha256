# Phase 151 — status porcelain v2 and NUL framing

Phase 151 extends the stage-aware status work from Phase 150 with Git's richer
machine-readable porcelain version 2 protocol.

## Porcelain v2

The installed CLI now accepts both canonical and numeric spellings:

```bash
pygit status --porcelain=v2
pygit status --porcelain=2
```

Ordinary tracked entries use Git's type-1 record shape:

```text
1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
```

Unmerged entries use the dedicated `u` record shape:

```text
u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>
```

Because pygit is SHA-256-native, object-name fields are 64 hexadecimal digits.
Missing objects use 64 zeroes and missing modes use `000000`.

Phase 150's conflict classifier remains authoritative for the seven legal
unmerged XY values: `DD`, `AU`, `UD`, `UA`, `DU`, `AA`, and `UU`.

## Branch headers

`--branch` with porcelain v2 emits the extensible Git-style headers:

```text
# branch.oid <commit> | (initial)
# branch.head <branch> | (detached)
# branch.upstream <upstream>
# branch.ab +<ahead> -<behind>
```

The OID is pygit's full SHA-256 commit ID. Upstream and ahead/behind lines are
included when the repository's existing status/upstream resolver can determine
them.

## NUL-framed machine output

`-z` is now supported by the modern status renderer.

```bash
pygit status -z
pygit status --porcelain=v1 -z
pygit status --porcelain=v2 -z
pygit status --porcelain=v2 --branch -z
```

When no explicit format is supplied, `-z` implies porcelain v1, matching Git.
Every emitted record is NUL-terminated. For porcelain v2 this includes optional
`# branch.*` header records as well as file records.

With `-z`, paths are emitted byte-for-byte as UTF-8 path text with no C-style
quoting. Without `-z`, unusual bytes are quoted with Git-style C escapes,
including octal escapes for non-ASCII UTF-8 bytes.

## Tracked plus untracked duplicate pathname

A staged deletion may coexist with an untracked worktree file at the same path
(for example after an index-only removal). Phase 150 stored v1 records in a
single path-keyed map and could collapse those two facts. Phase 151 preserves
Git's two-record representation:

```text
D  path
?? path
```

Porcelain v2 likewise emits both the type-1 staged deletion and the `?` record.

## Python API

`pygit.status_cli.porcelain_v2_records()` builds the protocol records without
writing to stdout. It accepts `branch`, `ignored`, and `zero` flags so tests and
integrations can inspect the exact record stream.

## Compatibility notes

The v2 `<sub>` field is `N...` for ordinary files. Pygit marks gitlink-mode
entries as submodules (`S...`), but detailed nested submodule dirty/untracked
state remains outside this phase.

Rename/copy type-2 (`2 ...`) records also remain a separate follow-up because
status rename detection is not yet part of the status engine.

## Regression coverage

`tests/test_phase151.py` covers:

- exact type-1 fields for staged additions and unstaged modifications;
- SHA-256 branch headers and ahead/behind values;
- initial and detached HEAD branch headers;
- full `u` records for `UU` conflicts;
- asymmetric `UD` conflicts with zero-filled missing stage metadata;
- untracked and ignored v2 items;
- Git-style non-`-z` pathname quoting;
- NUL framing for both headers and raw paths;
- implicit porcelain v1 under bare `-z`;
- simultaneous staged-deleted and untracked records for one pathname.

Reference behavior follows the current `git-status` porcelain-v2 specification,
including type-1/unmerged formats, branch headers, zero framing, and pathname
rules.
