# Phase 206 — shallow native round-trip and tag auto-follow

Phase204 / PR #181 made CLI `clone --depth` genuinely bandwidth-saving by
allowing imported commits to preserve native Git parent identities in stable
`parent-sha1` headers. Phase206 closes two follow-up gaps in that model:

1. exporting a stable foreign commit back to native Git must preserve its
   original parent lines even while those parents are absent locally; and
2. initial shallow clone should auto-follow tags whose targets are already
   inside the fetched shallow graph without letting tags defeat the requested
   depth.

Phase205 is intentionally occupied by the independent server-option stack in PR
#182, so this work uses Phase206 and remains stacked directly on Phase204.

## Stable foreign commit export

The historical `NativeExporter` serializes commit parents from
`CommitObject.parents`. For a Phase204 shallow boundary commit, that runtime list
is intentionally empty until every direct native parent has arrived. Exporting
such a commit with the historical path would therefore drop the native parent
headers and produce a different Git SHA-1.

Phase206 installs a narrow extension on the existing `NativeExporter` class:

- ordinary pygit commits continue through the historical exporter unchanged;
- commits with `native_parents is not None` serialize those preserved 40-hex
  SHA-1 parent ids verbatim;
- no synthetic local parent object is invented for a missing shallow parent;
- if all direct parents have been resolved locally, they are recursively
  exported as normal so a push to another remote can include their object graph;
- each resolved parent's reconstructed native id must match the preserved
  parent id or export fails explicitly rather than sending an inconsistent pack;
- the foreign child commit remains content-addressed in pygit by SHA-256 while
  its reconstructed native Git object regains the original SHA-1.

This means a depth-1 imported commit can round-trip to its original native
commit object even though its parent object is not present in `.pygit/objects`.
After a deepen operation brings the parent in, exporting the same local child
still produces the same native SHA-1 while now also being able to include the
parent graph when required.

The extension is installed from `pygit.__init__` in the same style as existing
repository compatibility installers, so direct `NativeExporter` use and
`Repository.push()` both see the behavior without changing the exporter API.

## Conservative shallow tag auto-follow

Protocol-v2 `ls-refs` already requests `peel`, so annotated tags are represented
as both:

```text
refs/tags/v1        <tag-object-sha1>
refs/tags/v1^{}     <peeled-target-sha1>
```

Phase206 considers a tag eligible only when its fully peeled target is already
present in the imported shallow graph. A lightweight tag is equivalent to its
own target object and therefore follows the same rule.

This gives the following behavior:

- lightweight tags to already imported objects are installed immediately with
  no second object transfer;
- annotated tags whose peeled target is already imported are fetched in one
  small follow-up protocol-v2 request if the tag object itself is missing;
- annotated tags whose peeled target lies behind the shallow boundary are not
  requested;
- the follow-up request re-declares the current native shallow boundary and
  advertises imported commits as haves;
- a tag-only follow-up is forbidden from returning `shallow` / `unshallow`
  updates, preventing tag auto-follow from mutating repository depth;
- newly imported annotated tag objects are recorded in the normal native map and
  stored as real SHA-256 `TagObject` objects.

This deliberately prioritizes shallow-depth integrity over fetching every
advertised tag.

## SHA-256 / SHA-1 boundary

Repository-visible identities remain SHA-256:

- branch refs;
- tag refs;
- commit/tree/blob/tag objects;
- `.pygit/shallow`;
- local history traversal.

Native SHA-1 remains limited to Git interoperability metadata:

- protocol-v2 refs and peeled tag ids;
- preserved `parent-sha1` foreign headers;
- native maps;
- upload-pack wants/haves;
- receive-pack/exported native objects.

## Regression coverage

`tests/test_phase206.py` covers:

- a genuinely truncated commit whose parent object is absent, proving its native
  parent header and original Git SHA-1 survive export;
- importing the missing parent later and proving the child local SHA-256 and
  exported native SHA-1 both remain stable while the parent is now included in
  the outgoing native graph;
- tag selection that includes reachable lightweight/annotated tags and excludes
  tags whose peeled target is outside the shallow graph;
- a depth-1 clone fixture that performs one branch fetch plus one small annotated
  tag fetch, keeps the shallow boundary unchanged, writes the lightweight and
  annotated tag refs, and excludes an unreachable deep tag.

## Compatibility boundary

Phase206 does not reconcile the independent Phase203/205 server-option line.
That stack can be rebased or reconciled after the shallow object-model work is
stable. No existing shallow/fetch/clone CLI option is removed or renamed.
