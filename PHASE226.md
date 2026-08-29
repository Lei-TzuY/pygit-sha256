# Phase 226 — Promisor-aware history content batching

Phase226 removes partial-clone demand-fetch waterfalls from history readers that consume commit trees outside the Phase225 `Repository.diff()` wrapper.

## Scope

The phase covers:

- `Repository.show(target)` — prefetch the selected commit plus its first parent before the historical `show` implementation flattens both snapshots and calls `_render_diff()`;
- `Repository.log(..., line_range=...)` — plan the commit snapshots that can reach `-L` content comparison and bulk materialize their unresolved blobs before the historical traversal begins;
- `Repository.log(..., follow=...)` — use the same planner because rename-follow mode also flattens current/parent trees while walking history.

Plain metadata-only `log()` remains on the historical zero-blob path.

## Why full snapshots are still required

The current tree flatteners return local SHA-256 blob ids. A foreign partial-clone tree entry can remain represented by only its native Git SHA-1 while its blob is promised. Therefore `show`, `log -L`, and `log --follow` cannot safely flatten a tree until every retained entry in the consumed snapshots has a real content-derived local SHA-256 id.

Phase226 does not invent surrogate ids and does not claim path-only materialization. It converts the existing one-request-per-entry behavior into one deduplicated bulk demand fetch. A future mixed native/local comparison representation could narrow the object set further.

## History planner

The `log` planner walks commit metadata only and mirrors the parts of historical `Repository.log()` that run before content access:

- start revision / HEAD selection;
- all-local-branches seed selection;
- shallow-boundary traversal stopping;
- first-parent traversal;
- author and grep filters;
- since / until filters;
- merge-only and parent-count filters.

For every commit that can reach content inspection, the planner includes the current snapshot and first parent because both `-L` and rename-follow logic may consume that parent while processing the current commit.

`max_count` intentionally does not truncate the prefetch plan. Historical `log` applies that count after content filtering; predicting the stopping point would itself require reading the promised content.

## Compatibility

- invalid `show` revisions continue through the historical revision/error seam before any network request;
- ordinary repositories remain network-free;
- partial-clone `log()` without `line_range` or `follow` remains blob-free;
- Phase213 single-object materialization behavior remains delegated to the existing materializer;
- Phase221 multi-promisor fallback and batch shrinking remain unchanged;
- Phase222 primary-promisor-last ordering remains unchanged;
- per-remote `serverOption` forwarding remains unchanged;
- no protocol request grammar, pack format, tree serialization, ref, index, or SHA-256 identity changes are introduced.

## Tests

`tests/test_phase226.py` covers:

- one bulk request for `show` across commit/parent snapshots;
- ordered `serverOption` forwarding through history demand-fetch;
- one bulk request for `log -L` across reachable history;
- the same planner for `log --follow`;
- metadata filters avoiding unnecessary blob prefetch;
- plain metadata-only partial-clone `log` remaining blob-free;
- ordinary `show` and `log -L` remaining network-free.
