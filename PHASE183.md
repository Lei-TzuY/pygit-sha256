# Phase 183 — Fetch pruning and tag policy

Phase 183 extends the configured fetch path from Phase181/182 with Git-style stale-ref pruning and tag selection policy while preserving pygit's SHA-256-native object model.

## CLI

The modern fetch command now accepts:

```text
pygit fetch --prune origin
pygit fetch --no-prune origin
pygit fetch --prune-tags origin
pygit fetch --no-prune-tags origin
pygit fetch --tags origin
pygit fetch --no-tags origin
```

Short forms match Git where applicable: `-p` for prune, `-P` for prune-tags, `-t` for tags, and `-n` for no-tags.

## Policy precedence

Pruning follows Git's remote-over-global configuration hierarchy, with explicit CLI flags winning over configuration:

```text
--prune / --no-prune
remote.<name>.prune
fetch.prune
false
```

Tag pruning uses the same structure:

```text
--prune-tags / --no-prune-tags
remote.<name>.pruneTags
fetch.pruneTags
false
```

Tag selection is:

```text
--tags / --no-tags
remote.<name>.tagOpt = --tags | --no-tags
automatic tag following
```

Invalid boolean or tagOpt values fail instead of silently selecting a different policy.

## Refspec-aware pruning

Pruning is driven by the configured fetch destination domain, not by every local ref with a similar name.

For the normal mapping:

```text
+refs/heads/*:refs/remotes/origin/*
```

`--prune` removes stale `refs/remotes/origin/*` refs whose corresponding source branch is no longer advertised. Negative source refspecs are respected.

Phase182's intentionally empty tracked-branch state remains authoritative: if Git-style `remote.<name>.url` exists but `remote.<name>.fetch` has been cleared by `remote set-branches`, Phase183 does not recreate the historical all-heads fallback. Only legacy JSON-only remotes retain that compatibility fallback.

## Tags

Normal fetch runs in automatic tag-following mode. A missing tag is followed only when the tag points at an object already known from the selected remote history, including objects imported by the current fetch. This prevents an unrelated remote tag from widening an otherwise single-branch fetch.

Lightweight tags whose target is already known require no extra transfer. Annotated tags are handled in two stages: once their peeled target is known, the configured fetch path requests only the missing tag object and imports it through the existing native SHA-1 to local SHA-256 converter.

`--no-tags` disables automatic following but does not cancel an explicit `refs/tags/*` fetch mapping.

`--tags` adds an explicit non-forced tag mapping. Existing differing local tags are protected and cause a `would clobber existing tag` style failure.

`--prune-tags` acts like a forced `+refs/tags/*:refs/tags/*` mapping. It can therefore fetch/update tags even without `--prune`; deletion of stale local tags occurs only when pruning is also enabled.

A key compatibility boundary is preserved:

- `--prune --tags` fetches all tags but does **not** make tags part of the prune domain.
- `--prune --prune-tags` fetches/updates all remote tags and prunes local tags absent from the remote.

## Transport architecture

The mature `SmartHttpClient.fetch()` API remains unchanged. Phase183 narrows an `Advertisement` to the selected ref OIDs before passing it into the existing upload-pack client, so the existing protocol implementation naturally emits only the required `want` lines.

The existing `NativeImporter` and per-remote native SHA map continue to own the SHA-1/SHA-256 conversion boundary. Automatic annotated-tag following may perform a second narrowly scoped upload-pack request for tag objects whose peeled targets became known after the branch fetch.

The legacy `Repository.fetch()` API is deliberately unchanged. Modern `fetch` and remote-backed `pull` continue through `fetch_configured()`.

## Git compatibility checked

Current Git fetch documentation specifies that:

- `--prune` removes stale refs according to the active fetch refspec destination domain;
- tags fetched only through automatic following, or merely because `--tags` was used, are not implicitly pruned;
- `--prune-tags` is equivalent to supplying the tag refspec for pruning purposes and may be specified without `--prune`;
- `--no-tags` disables automatic tag following;
- `remote.<name>.tagOpt`, `fetch.prune`, `remote.<name>.prune`, `fetch.pruneTags`, and `remote.<name>.pruneTags` provide persistent defaults.

Native Git 2.47.3 probes additionally confirmed:

- automatic following can create a newly advertised tag whose target object is already locally known;
- `remote.origin.tagOpt=--no-tags` disables that behavior;
- explicit `--tags` overrides no-tags configuration;
- `fetch --prune --tags` preserves a local-only tag;
- `fetch --prune --prune-tags` deletes local tags absent from the remote;
- `fetch --tags` refuses to replace a differing existing local tag;
- automatic following also leaves a differing existing local tag untouched.

## Regression coverage

`tests/test_phase183.py` covers:

- wildcard fetch-refspec mapping in both source/destination directions;
- preservation of Phase182's intentionally empty fetch-list state;
- CLI/remote/global prune precedence;
- tagOpt and CLI precedence;
- stale remote-tracking branch pruning;
- the `--tags` versus `--prune-tags` prune-domain distinction;
- prune-tags without prune;
- no-tags suppression;
- lightweight and annotated automatic tag following;
- skipping unrelated tags;
- tag clobber protection and forced prune-tags updates;
- explicit tag mappings surviving no-tags;
- fetch CLI flag forwarding and prune summary output.

## SHA-256-native design

All stored refs and objects remain SHA-256-native. Refspec planning and prune/tag policy operate on names and the existing per-remote SHA map; no local object format, index format, pack format, or public transport boundary is changed.
