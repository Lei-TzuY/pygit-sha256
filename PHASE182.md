# Phase 182 — remote set-branches

Phase182 makes the branch-selection side of Git-style remote configuration user-facing. Phase181 already taught modern fetch/pull to honor `remote.<name>.fetch`; this phase adds the matching `git remote set-branches` porcelain and fixes the important distinction between a legacy remote with no Git-style config and a configured remote whose fetch list was intentionally cleared.

## Commands

```text
pygit remote set-branches <name> <branch>...
pygit remote set-branches --add <name> <branch>...
```

Without `--add`, the existing `remote.<name>.fetch` values are replaced by one mapping per branch:

```text
+refs/heads/<branch>:refs/remotes/<name>/<branch>
```

With `--add`, new mappings are appended in argument order. Duplicate branch arguments are preserved, matching native Git's multi-valued configuration behavior. An empty replacement list removes every fetch mapping; an empty `--add` is a no-op.

Branch tokens are treated literally, just like native `git remote add -t`: wildcard text is written through to the generated refspec, and this command does not try to pre-validate a conventional branch name. Invalid refspecs therefore fail later when fetch parses/uses them, as they do in native Git.

## Fetch behavior

Phase181 used an all-heads compatibility fallback when `remote.<name>.fetch` was absent. That remains correct for repositories created by older pygit versions that have only historical `.pygit/config.json` remote metadata.

For a Git-style configured remote, however, an absent fetch key can be intentional: `remote set-branches <name>` with no branches clears the list. Phase182 therefore distinguishes those cases:

- legacy JSON-only remote with no Git-style URL: fall back to `+refs/heads/*:refs/remotes/<name>/*`;
- Git-style remote with URL but no fetch values: select no remote branches;
- Git-style remote with fetch values: honor those exact/wildcard/negative source selectors.

Tags retain the existing automatic tag-import behavior from the fetch stack. Ordinary `set-branches` is metadata-only: it does not fetch immediately and does not delete tracking refs that were created earlier. A later prune/removal operation remains responsible for stale tracking refs.

## Git compatibility probes

Native Git 2.47.3 local probes confirmed:

- `remote set-branches origin main dev` replaces the default wildcard with two exact fetch mappings;
- `remote set-branches --add origin release` appends a third mapping;
- repeated branch arguments remain repeated config values;
- `feature/*` is written as a wildcard mapping;
- calling `remote set-branches origin` with no branches succeeds and removes all fetch mappings;
- calling `remote set-branches --add origin` with no branches succeeds without changing the list;
- unknown remotes fail;
- branch tokens are written literally rather than normalized as full refs.

These behaviors match the current `git-remote` documentation, which defines `set-branches` as changing the list of branches tracked by a named remote, interpreting each branch as though supplied with `remote add -t`, and making `--add` append rather than replace.

## SHA-256-native design

Phase182 changes only remote configuration and fetch ref selection. Local object IDs, refs, object serialization, index data, packs, native SHA maps, and the SHA-256-native to native SHA-1 smart-HTTP conversion boundary remain unchanged.

## Regression coverage

`tests/test_phase182.py` covers replacement, append, duplicate preservation, literal/wildcard branch tokens, empty-list clearing, empty `--add`, unknown remotes, tracking-ref preservation, configured fetch selection, the intentionally empty fetch list, legacy JSON-only fallback, and CLI replace/add round trips.
