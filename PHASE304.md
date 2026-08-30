# Phase 304: strict protocol-v2 object-info record grammar

Phase 304 tightens the textual record grammar accepted by the protocol-v2
`object-info size` response parser while preserving native Git behavior.

## Motivation

Git's published protocol-v2 grammar describes object-info output as:

```text
output = info flush-pkt
info = PKT-LINE(attrs LF)
       *PKT-LINE(obj-info LF)
attrs = attr | attrs SP attrs
attr = "size"
obj-info = obj-id SP obj-size
```

The parser already required the Phase294 flush-terminated envelope, but it used
`rstrip(b"\n")` for textual records. That could silently normalize repeated
trailing newlines and other ambiguous packet text.

The first Phase304 CI run also provided an important compatibility probe: native
Git 2.55.0 `upload-pack --stateless-rpc` emitted the `size` object-info record
without an LF. Git compatibility therefore takes precedence over interpreting
the documentation as requiring an LF byte in every real packet.

## Behavior

Phase304 accepts both observable record forms:

- native Git packet text without a terminal LF;
- the documented form with exactly one terminal LF.

It still rejects:

- CR or CRLF endings;
- embedded or repeated LF bytes;
- object result records with additional space-separated fields;
- non-ASCII object-info text.

The valid `size`, `size\n`, `<oid> <decimal-size>`, `<oid> <decimal-size>\n`,
`<oid> `, and `<oid> \n` forms therefore remain interoperable without reverting
to unrestricted `rstrip()` normalization.

The existing complete-envelope checks remain unchanged: responses still require
one final flush packet and reject delimiter/response-end terminators or trailing
bytes after the flush.

## Git compatibility

The full suite retains the native Git object-info round-trip. Initial Tests
#2653 intentionally exposed the documentation/native discrepancy by failing only
that probe: Python 3.9 reached 2249 passed / 1 failed when native Git 2.55.0
returned `size` without LF. The follow-up parser rule is based on that observed
native behavior while remaining strict about ambiguous endings and result
fields.

## SHA-256-native boundary

This phase changes response validation only. Object-info requests still carry
genuine remote-native full 40-hex SHA-1 identities and return scalar size
metadata. It introduces no SHA-1 padding, truncation, translation, surrogate
SHA-256 identity, local object creation, content materialization, or
metadata-only native-to-local mapping.

## Coordination

Phase304 is based on the exact-green Phase301 head
`f2f1eb0c2426dafa6de8f655f45870bdfd64689d`. Phase303 was already occupied by
independent strict capability/ls-refs record work in `pygit/protocol_v2.py`, so
this phase deliberately stays in `pygit/protocol_v2_object_info.py` and does not
overwrite that sibling branch.
