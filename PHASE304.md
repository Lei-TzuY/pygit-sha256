# Phase 304: strict protocol-v2 object-info record grammar

Phase 304 tightens the textual record grammar accepted by the protocol-v2
`object-info size` response parser.

## Motivation

Git's protocol-v2 grammar defines object-info output as:

```text
output = info flush-pkt
info = PKT-LINE(attrs LF)
       *PKT-LINE(obj-info LF)
attrs = attr | attrs SP attrs
attr = "size"
obj-info = obj-id SP obj-size
```

The parser already required the Phase294 flush-terminated envelope, but it used
`rstrip(b"\n")` for textual records. That accidentally accepted records with no
LF terminator, repeated trailing LFs, and other framing that is outside the
specified command grammar.

## Behavior

Phase 304 now requires every textual object-info pkt-line to contain exactly one
terminal LF. It rejects:

- missing LF terminators;
- CRLF records;
- embedded or repeated LF bytes;
- object result records with additional space-separated fields.

The valid `size\n`, `<oid> <decimal-size>\n`, and `<oid> \n` forms remain
accepted. The last form preserves the existing representation of an explicitly
unknown requested object.

The existing complete-envelope checks remain unchanged: responses still require
one final flush packet and reject delimiter/response-end terminators or trailing
bytes after the flush.

## Git compatibility

This follows the published Git protocol-v2 object-info grammar rather than
accepting relaxed synthetic packet text. The existing native Git
`upload-pack --stateless-rpc` object-info round-trip remains the end-to-end
compatibility probe in the full suite.

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
