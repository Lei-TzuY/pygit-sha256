# Phase 174 — annotated-tag native export

Phase 174 closes the remaining annotated-tag gap at pygit's SHA-256-native → native Git SHA-1 push boundary.

## What changed

`NativeExporter` now accepts `TagObject` in addition to blobs, trees, and commits.

When exporting an annotated tag it:

- recursively exports the tag target first;
- rewrites the tag object's `object <oid>` header from pygit's internal SHA-256 object ID to the corresponding native SHA-1 object ID;
- preserves the declared target type, tag name, tagger identity/timestamp/timezone, and annotation message;
- emits a regular native `tag` object whose object ID is computed from the canonical `tag <size>\0<payload>` representation;
- supports nested annotated tags by recursively converting tag-to-tag targets;
- reuses an already-known native target mapping when that target is known to exist remotely, avoiding unnecessary resend of the target graph.

No local object, index, or ref format changes are introduced. Annotated tags remain SHA-256-native in pygit's object store; conversion happens only at the existing smart-HTTP native boundary.

## Git compatibility

Git's pack format defines tag objects as regular object type `OBJ_TAG` (type number 4), alongside commit/tree/blob. Packed objects omit the loose-object prefix from the compressed payload, but object IDs are still computed from the reconstructed canonical `<type> <size>\0` prefix plus payload.

A native annotated tag payload contains an `object` header naming the target object. Because a native SHA-1 repository cannot consume pygit's internal SHA-256 target ID, that field must be rewritten to the exported native target OID before hashing and packing the tag.

The existing `build_pack()` and `PackParser` already supported native tag type 4; Phase 174 wires `TagObject` into `NativeExporter` so explicit annotated-tag pushes and Phase 173 `--follow-tags` can use that path end-to-end.

## Regression coverage

`tests/test_phase174.py` covers:

- canonical annotated-tag payload rewriting;
- native SHA-1 tag object ID calculation;
- nested annotated-tag conversion;
- reuse of known remote target mappings;
- pack type-4 round-trip through `build_pack()` / `PackParser`;
- target-aware `push_ref()` transport of a real local annotated tag.

## Stack coordination

This phase is stacked directly on Phase 173 / PR #149 exact head `251f52e6c38e16a836a33a52b7edd033c1b7cf57`. The independent `main` line remains unchanged and is not overwritten or rebased into this push stack.
