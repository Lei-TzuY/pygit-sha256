# Phase 209 — protocol-v2 clone server options

Phase209 extends the green Phase208 stack with Git-compatible clone-time protocol-v2 `--server-option` forwarding without changing pygit's repository-visible SHA-256 identity model.

## Clone CLI

`pygit clone` now accepts repeatable `--server-option=OPTION` / `--server-option OPTION` values. Values are kept in command-line order and NUL/LF characters are rejected before any repository or network side effects.

Git clone already reserves `-o` for choosing the remote name, so Phase209 deliberately does not reuse fetch's historical `-o` server-option alias on the clone command.

## Ordinary clone path

An ordinary clone with one or more server options keeps the established `Repository.clone` and `NativeImporter` path. Phase209 wraps that operation in the reconciled Phase207 `protocol_v2_transport(server_options=...)` context instead of introducing another clone importer.

This preserves historical local SHA-256 object identities while changing only the smart-HTTP transport metadata. Because server options are protocol-v2-only, the existing transport rejects a protocol-v0 fallback rather than silently discarding the requested metadata.

When no server option is supplied, the exact pre-Phase209 ordinary clone call path remains unchanged.

## True shallow clone path

`clone --depth` continues to use the Phase204 true shallow transport and Phase206 stable native-parent importer. Phase209 adds an optional `server_options` argument to `clone_shallow_repository` and constructs its `SmartHttpV2FetchClient` with those ordered values.

The same client instance performs:

1. protocol-v2 `ls-refs` discovery,
2. the initial depth-limited `fetch`, and
3. any Phase206 annotated-tag auto-follow fetch.

Because `SmartHttpV2FetchClient` stores server options on the client and Phase207 emits them in every command capability-list, all three command requests receive the same option sequence. The tag follow-up still re-declares the shallow boundary and is still forbidden from deepening history.

When no server options are supplied, shallow clone constructs `SmartHttpV2FetchClient(url)` with the exact historical call shape so Phase204/206 monkeypatch and caller seams remain compatible.

## Coordination with Phase208

Phase208 / PR #185 adds fetch-side `--shallow-since` and `--shallow-exclude`. Phase209 stacks on its exact green head and does not modify that selector layer. Clone server options therefore land on the current reconciled server-option + shallow stack rather than on an older transport branch.

## Configuration scope

Current Git documentation also permits `remote.<name>.serverOption` fallback when clone has no CLI server option. pygit does not yet have a pre-clone global/system configuration layer from which a not-yet-created clone repository can resolve that setting, so Phase209 intentionally implements the explicit clone CLI form first rather than inventing repository-local configuration before the repository exists.

## SHA-256-native design

Server options are request metadata only. Ordinary clone keeps the historical native-to-local importer, true shallow clone keeps the stable foreign-parent importer, refs remain local SHA-256, and native SHA-1 remains confined to Git smart-HTTP interoperability and native maps.

## Verification target

- base: Phase208 / PR #185 exact head `917385b561c0772d9adfbf0cd35ae9c17ccb8ff8`
- Phase208 GitHub Actions Tests #1865: success on Python 3.9 / 3.13 before Phase209 branch creation
- focused regressions cover option validation/order, ordinary clone v2 scoping, no-option transparency, depth forwarding, Phase204 call-shape compatibility, and shared optioned shallow-client reuse across discovery/fetch/tag phases
- full Python 3.9 / 3.13 GitHub Actions matrix remains the final gate
