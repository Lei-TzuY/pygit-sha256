# Phase294: Strict protocol-v2 object-info response framing

Phase294 hardens the metadata-only `object-info size` parser so it accepts only a complete response envelope defined by Git protocol v2.

## Motivation

Git documents an `object-info` response as:

```text
output = info flush-pkt
```

The previous parser stopped at either `flush-pkt` or `response-end-pkt` and returned the metadata parsed so far. It also ignored any bytes that followed the first accepted terminator. That made it possible for a truncated or ambiguously framed response prefix to be treated as trusted size metadata.

Because these sizes feed partial-clone `blob:limit` classification, malformed framing must be rejected before scalar metadata can become persistent trusted state.

## Behavior

`parse_object_info_size_response()` now:

- requires a real `flush-pkt` terminator;
- rejects `delim-pkt` and `response-end-pkt` in the object-info response envelope;
- rejects responses that end without a flush packet;
- rejects every byte after the flush packet instead of silently ignoring it;
- preserves existing validation for the `size` attribute, OID syntax, duplicate OIDs, unknown OIDs, and numeric sizes.

The smart-HTTP client already routes parser `ValueError` through the existing failed-client eviction path, so malformed framing remains a soft remote failure to the higher-level metadata refresh logic rather than introducing content fallback.

## Git compatibility

Current Git protocol-v2 documentation specifies `object-info` output as info pkt-lines followed by `flush-pkt`. Phase294 follows that command-specific grammar instead of treating the generic protocol-v2 `response-end-pkt` as interchangeable.

No request grammar changes are introduced.

## SHA-256-native boundary

Phase294 changes only response-envelope validation. Object-info requests still use genuine remote-native full SHA-1 OIDs at the transport boundary, and only scalar size metadata can be returned to the promisor layer. No SHA-1 padding or translation, surrogate SHA-256 identity, content materialization, local object creation, or native-to-local mapping is introduced.

## Tests

`tests/test_phase294.py` covers:

- truncated response without `flush-pkt`;
- `response-end-pkt` rejection;
- delimiter rejection;
- trailing pkt-line data after a flush packet;
- the valid exact flush-terminated envelope.

The existing Phase275 native Git object-info round-trip remains the compatibility probe for a real Git server response.
