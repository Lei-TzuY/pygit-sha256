# Phase 178 — Remote URL porcelain and fetch URL precedence

Phase 178 builds on Phase 177's multi-destination push support by making remote
URL lists manageable through pygit's own command line.

## User-facing commands

The installed command now supports:

```text
pygit remote get-url [--push] [--all] <name>
pygit remote set-url [--push] <name> <new-url> [<old-url-regex>]
pygit remote set-url --add [--push] <name> <new-url>
pygit remote set-url --delete [--push] <name> <url-regex>
```

`get-url` prints only the first URL by default. `--all` prints every URL in
configuration order. `--push` selects the push URL namespace; if no pushurl is
configured, querying push URLs falls back to the ordinary remote URLs just as
`git remote get-url --push` does.

`set-url` replaces the first URL by default, or the first URL matching the
optional old-URL regular expression. A missing match is an error and leaves the
configuration unchanged. `--add` appends a URL. `--delete` removes every URL
matching its regular expression. Deleting every ordinary/fetch URL is rejected,
while deleting every explicit push URL is valid and restores push fallback to
the ordinary URLs.

## Fetch versus push URL precedence

Git permits more than one `remote.<name>.url` value. Fetch operations use only
the first URL. Push operations use every ordinary URL unless one or more
`remote.<name>.pushurl` values exist, in which case every pushurl is used
instead.

Phase 178 centralizes these rules in `pygit.remote_urls`:

- `fetch_urls()` returns the ordered ordinary URL list.
- `fetch_url()` returns its first element.
- `push_urls()` returns explicit pushurls or all ordinary URLs as fallback.
- Phase 177's `remote_push_urls()` now delegates to the shared resolver.
- configured-name `ls-remote` resolution now uses the first fetch URL rather
  than the historical JSON-only URL.

The repository predates Git-style multi-valued config and historically stores
one remote URL in `.pygit/config.json`. That value remains the fallback for old
repositories. On a fetch-URL `set-url` mutation, Phase 178 also synchronizes the
first URL back to this legacy field so existing `Repository.fetch()` and
`prune_remote()` transport paths immediately use the same first destination
without changing their public APIs.

## Configuration storage

Multi-valued URLs are stored in pygit's existing flattened local config form:

```ini
[remote]
origin.url = https://one.example/repo.git
origin.url = https://two.example/repo.git
origin.pushurl = https://push-one.example/repo.git
origin.pushurl = https://push-two.example/repo.git
```

Mutations replace only the selected flattened multivar and preserve unrelated
sections and keys. Phase 177's duplicate-key-aware `GitConfig.get_all()` remains
the reader.

## Git compatibility

Current Git documentation specifies that:

- `remote get-url` prints the first URL unless `--all` is supplied;
- `--push` queries push URLs instead of fetch URLs;
- `set-url` replaces the first matching URL, or the first URL when no old-URL
  regex is supplied;
- a missing old-URL match is an error with no mutation;
- `--add` appends rather than replacing;
- `--delete` removes all matching URLs;
- deleting all non-push URLs is forbidden;
- multiple ordinary URLs use the first for fetch and all for push unless
  pushurls exist.

Native Git 2.47.3 probes additionally confirmed that `remote get-url --push
--all` falls back to all ordinary URLs when no pushurl exists, an explicit
pushurl replaces that fallback, a missing replacement regex exits non-zero, and
attempting to delete every fetch URL exits non-zero without changing the list.

## SHA-256-native design

This phase changes only endpoint selection and local configuration metadata. It
does not modify object IDs, object serialization, refs, index data, pack
conversion, native SHA maps, or the SHA-256-native to SHA-1 smart-HTTP boundary.

## Regression coverage

`tests/test_phase178.py` covers:

- first/all fetch URL presentation;
- push URL fallback and explicit override;
- first fetch URL resolution for configured `ls-remote` names;
- default first-URL replacement and legacy endpoint synchronization;
- regex replacement of only the first match;
- no-mutation behavior on a missing regex match;
- legacy URL materialization when `--add` first creates a multivalue list;
- deletion of every matching URL and first-URL refresh;
- rejection of deleting all fetch URLs;
- valid deletion of all pushurls followed by ordinary-URL fallback;
- CLI get/set/add/push round trips.
