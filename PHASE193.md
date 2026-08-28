# Phase 193 — fetch `--set-upstream`

Phase193 adds Git-style upstream tracking configuration after a successful fetch.

## Supported behavior

- `pygit fetch --set-upstream origin main`
- source-only and `src:dst` branch refspecs both use the source branch as the upstream merge ref
- direct HTTP(S) URLs may be recorded as `branch.<name>.remote`
- tracking is written only after a successful fetch
- exactly one positive, non-wildcard `refs/heads/*` source must be named
- missing source branches and multiple source branches warn without failing the fetch or mutating tracking configuration
- detached HEAD warns and does not install tracking
- `--dry-run --set-upstream` exercises the same logic inside the Phase192 repository sandbox, so no tracking configuration persists
- a literal `--set-upstream` after the standard `--` option terminator remains a refspec token

For a current branch `local` fetching `origin main`, the resulting Git-style configuration is:

```ini
[branch]
    local.remote = origin
    local.merge = refs/heads/main
```

The project uses a flattened INI representation, but `configured_upstream()` exposes the same logical `branch.<name>.remote` and `branch.<name>.merge` contract used by pull/fetch default resolution.

## Git compatibility

Current Git fetch documentation defines `--set-upstream` as adding an upstream tracking reference after a successful remote fetch. Native Git 2.47.3 local probes confirmed:

- one explicit branch installs tracking
- no explicit source branch succeeds with a warning and does not install tracking
- multiple explicit branches succeed with a warning and do not install tracking
- a direct URL can be stored as the branch remote
- detached HEAD succeeds with a warning and does not install tracking

## SHA-256-native design

This phase changes configuration only. Object storage, local refs, `FETCH_HEAD`, native SHA maps, pack conversion, and the smart-HTTP SHA-1 interoperability boundary are unchanged.
