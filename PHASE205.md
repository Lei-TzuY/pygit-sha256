# Phase205: fetch remote serverOption config fallback

Phase205 extends Phase203's explicit protocol-v2 `-o/--server-option` support with Git's named-remote configuration fallback.

## Behavior

When the command line contains no `-o` / `--server-option`, `pygit fetch` now reads repeated `remote.<name>.serverOption` values for the named remote being fetched and sends them, in configuration order, as protocol-v2 `server-option=<value>` request capabilities.

Command-line server options take complete precedence over configured values. A direct HTTP(S) URL is not a named remote and therefore does not inherit any `remote.*.serverOption` entry.

The policy is remote-scoped, not URL-scoped. During `--multiple`, `--all`, remote groups, or `fetch.all=true`, two named remotes may share the same URL while carrying different server-option lists. Protocol-v2 clients and fallback state are keyed by both active remote identity and URL so those policies cannot leak across remotes.

`fetch --negotiate-only` follows the same precedence: explicit CLI options win; otherwise the selected named remote contributes its configured server options. Direct-URL negotiate-only requests do not inherit named-remote configuration.

If an effective server option exists and the server ignores protocol v2, pygit fails instead of silently dropping the requested option. When no server option applies, the established protocol-version and v2-to-v0 fallback behavior is preserved.

## Git compatibility

Current Git fetch documentation states that `-o/--server-option=<option>` transmits ordered values only over protocol version 2, rejects NUL/LF in explicit option values, and uses `remote.<name>.serverOption` only when no command-line server option is supplied.

## SHA-256-native design

This phase changes request metadata selection only. Remote Git SHA-1 identities remain confined to smart-HTTP discovery, negotiation and pack transfer. Repository-visible objects, refs, FETCH_HEAD, reflogs and native-map-backed local identities remain SHA-256.

## Verification targets

- ordered multi-valued remote configuration
- command-line precedence
- named-remote negotiate-only fallback
- direct-URL isolation
- shared-URL remote identity isolation
- strict v2 requirement when a configured server option is active
- full existing Python 3.9 / 3.13 regression suite
