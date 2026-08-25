# Phase 63: mktag plumbing

Phase 63 adds strict annotated-tag object construction on top of pygit's SHA-256 object store.

## CLI

```bash
cat tag-payload.txt | pygit mktag
```

`mktag` reads one annotated-tag payload from standard input, validates it, writes the exact payload as a `tag` object, and prints the resulting 64-hex SHA-256 object ID. It does **not** create or move `refs/tags/*`; callers may use existing ref plumbing separately.

The canonical payload shape is:

```text
object <64-hex-object-id>
type <blob|tree|commit|tag>
tag <tag-name>
tagger Name <email> <timestamp> <timezone>

<message>
```

## Validation

Unlike generic `hash-object -t tag`, `mktag` validates both syntax and repository connectivity before writing:

- exactly four ordered headers: `object`, `type`, `tag`, `tagger`
- lowercase 64-hex target object ID, matching pygit's canonical object-store paths
- supported target type (`blob`, `tree`, `commit`, or `tag`)
- tag name valid below `refs/tags/`
- canonical tagger identity and numeric timezone in `+HHMM` / `-HHMM` form
- LF-only UTF-8 payload
- target object must already exist, including packed-object lookup
- declared target type must match the actual stored object type

Validation failures do not write a tag object.

## Python API

```python
from pygit import make_tag, parse_tag_payload, validate_tag_payload

parsed, target_oid = parse_tag_payload(payload)
validate_tag_payload(repo, payload)
tag_oid = make_tag(repo, payload)
```

`make_tag()` writes the exact validated bytes through the typed object writer, so the returned object ID is identical to hashing the same payload as a `tag` object.

## Compatibility boundary

This remains pygit's native `.pygit` / SHA-256 object model. The command reproduces the useful `mktag` validation workflow but does not claim byte-for-byte compatibility with every native Git SHA-1/SHA-256 repository mode or external signature-verification policy.
