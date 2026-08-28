# Phase 207 — reconcile protocol-v2 server options with shallow transport

Phase203/205 and Phase202/204/206 evolved on parallel branches from Phase201.
The server-option line added explicit and configured protocol-v2 request metadata,
while the shallow line added depth negotiation, genuinely truncated initial clones,
stable foreign commit identity, and conservative tag auto-follow.

Phase207 reconciles those lines on top of Phase206 without merging the stale
Phase203/205 transport files wholesale.

## Why a manual reconciliation is required

Phase205 is based on Phase203, which itself is based on Phase201. Replacing the
latest protocol-v2 files with that branch would discard Phase202 shallow request
arguments and the later shallow object-model work.

There is also a semantic conflict: Phase205's outer configured-serverOption
transport intercepts `SmartHttpClient.fetch`. If copied unchanged, it would call
the v2 client without the active `shallow`, `deepen`, and `deepen-relative`
arguments, silently defeating shallow negotiation.

Phase207 therefore replays the server-option behavior into the current shallow
implementation and adds dedicated cross-feature regressions.

## Reconciled wire format

`pygit.protocol_v2` now provides a shared command capability-list builder that:

- emits `command=<name>`;
- emits the pygit agent only when advertised;
- preserves repeated server-option ordering;
- validates NUL/LF rejection;
- requires the server-advertised `server-option` capability;
- terminates the capability list with the protocol-v2 delimiter.

Both `ls-refs` and `fetch` use that shared prefix.

The current `fetch` request retains all Phase202 arguments:

- `shallow <oid>`;
- `deepen <n>`;
- `deepen-relative`;
- `wait-for-done` negotiation;
- ordinary wants/haves.

Server options and shallow arguments therefore coexist in one protocol-correct
request rather than competing wrapper paths.

## Explicit server options

`pygit fetch -o <value>` and `pygit fetch --server-option=<value>` retain Phase203
behavior:

- repeated values preserve order;
- values are removed before the legacy fetch parser sees them;
- parsing stops at `--`;
- explicit values force protocol v2;
- v0 fallback is rejected when an effective option would otherwise be lost;
- negotiate-only forwards the same ordered options.

Phase207 extracts server options before shallow options. This matters when a
server-option value itself looks like `--depth` or `--deepen`; metadata must not
be reparsed as a client fetch option.

## Configured per-remote options

`remote.<name>.serverOption` retains Phase205 semantics:

- config values apply only when no explicit CLI server option is supplied;
- duplicate/reset ordering is provided by the duplicate-key-aware GitConfig
  reader;
- direct HTTP(S) sources do not inherit named-remote config;
- remote identity remains part of the client/fallback cache key, so two remotes
  sharing one URL may carry independent option lists;
- `--multiple`, `--all`, remote groups, and negotiate-only keep named-remote
  behavior.

## Shallow-aware configured transport

The configured option transport now reads the active Phase202 shallow request.
For the first transfer of a remote it forwards:

- the remote's ordered server options;
- current native shallow boundaries;
- deepen depth;
- deepen-relative state;
- the existing negotiated haves.

The outer configured transport owns protocol routing for that command. The
frontend's generic inner protocol-v2 context is replaced with a no-op context,
while its shallow v2 guard is kept logically satisfied. The outer transport then
enforces the real requirement and raises if the server falls back to v0.

This preserves wrapper composition order:

1. configured protocol-v2 transport;
2. refetch/negotiation have policy;
3. shallow importer scope;
4. dry-run repository transaction.

## Compatibility

- ordinary protocol-v2 fetch without server options keeps the historical
  no-argument `protocol_v2_transport()` seam;
- ordinary v0 fallback remains available when no v2-only feature is active;
- shallow requests never silently fall back to v0;
- effective server options never silently fall back to v0;
- repository-visible objects, refs, FETCH_HEAD, shallow boundaries, and foreign
  commit identities remain SHA-256.

## Regression coverage

Phase207 restores the full Phase203 and Phase205 regression modules on the
latest stack and adds cross-feature tests for:

- a server-option value that looks like a shallow CLI option;
- request framing with server-option + shallow + deepen-relative together;
- explicit server options and an active shallow request reaching the same v2
  client;
- configured per-remote server options and an active shallow request reaching
  the same v2 client.

The full Python 3.9 / 3.13 GitHub Actions suite remains the final compatibility
gate.
