# Phase 379 — Protocol-v2 bundle-uri discovery

Phase379 adds the read-only discovery half of Git protocol-v2 `bundle-uri`.
It intentionally stops before following or downloading any server-provided URI.

## Motivation

Bundle URIs can seed a clone/fetch with pre-generated Git bundles and reduce the
amount of pack generation and transfer required from the origin.  The protocol
makes this optimization optional: a client must gracefully fall back to the
ordinary Git fetch path whenever the advertised list is missing, unsupported,
or unusable.

This phase therefore establishes a narrow transport/list trust boundary before
any additional network or repository mutation is considered.

## Public API

`pygit.protocol_v2_bundle_uri` adds:

- `BundleUriEntry`
- `BundleUriList`
- `build_bundle_uri_request()`
- `parse_bundle_uri_response()`
- `SmartHttpV2BundleUriClient.discover_bundle_uris()`

The client reuses the existing strict protocol-v2 capability discovery, server
option handling, smart-HTTP media-type validation, and pkt-line decoder.

## Wire contract

The server must advertise `bundle-uri`.  A future capability value is ignored,
as required by the protocol.

The command has no arguments:

```text
command=bundle-uri
0001
0000
```

The response is a sequence of `bundle.*=value` packet lines ending in exactly
one flush packet.  Framing errors are hard protocol failures: truncation,
delimiter/response-end packets, or bytes after the final flush are rejected.

Malformed textual records are discarded, matching the protocol's graceful
metadata rule.  The semantic list currently understands:

- `bundle.version=1`
- `bundle.mode=all|any`
- `bundle.heuristic=creationToken`
- `bundle.<id>.uri`
- `bundle.<id>.filter`
- `bundle.<id>.creationToken`
- `bundle.<id>.location`

Bundle ids are limited to documented alphanumeric/hyphen names.  Explicit
unsupported versions or modes, duplicate URIs, or named bundles without a URI
make the optional list unusable and return `None`; callers can continue with an
ordinary fetch.  Invalid creation tokens are ignored as optional heuristic
metadata.

For compatibility with current Git's protocol-list initializer, absent explicit
version/mode records use version 1 and mode `all`.

## Network boundary

`SmartHttpV2BundleUriClient` performs only:

1. protocol-v2 smart-HTTP capability discovery;
2. one `bundle-uri` upload-pack POST when the capability exists.

It does **not** issue HTTP requests to any advertised bundle URI.  A later phase
may add explicit download/header verification behind a separate resource and
repository trust boundary.

The upload-pack response must use
`application/x-git-upload-pack-result`; incorrect real HTTP media types fail
before body parsing.

## Native Git compatibility

Native Git 2.47.3 was probed with a bare repository configured with:

```text
uploadpack.advertiseBundleURIs=true
bundle.version=1
bundle.mode=all
bundle.primary.uri=https://bundles.example/repo.bundle
```

The server advertises a bare `bundle-uri` capability.  Its stateless-rpc command
returns the configured `bundle.*` key/value packet lines followed by `0000`.
Adding an argument after the delimiter terminates with:

```text
fatal: bundle-uri: unexpected argument
```

`tests/test_phase379.py` repeats the native stateless-rpc round trip on the CI
runner Git and parses the result through the public Phase379 parser.

## SHA-256-native invariants

This feature carries no object identity at all.  Bundle list metadata is not an
object map and no local identity is synthesized from a URI, config key, or
server metadata.

- no SHA-1 padding/truncation/translation
- no surrogate SHA-256
- no object-store writes
- no ref/HEAD/reflog changes
- no shallow or promisor mutation
- no automatic bundle download

Any object imported from a bundle in a future phase must still cross the
existing content-derived SHA-256 object boundary.

## Coordination

Phase379 was created from Phase331 / PR #308 exact-green head
`40dacfe1dd2f05d6fb67864d291523f3add21036` after rechecking that Phase379 was
free.  Active Phase377 clone and Phase378 loose-object durability branches are
independent and untouched.  The long Phase321–376 packfile-URI/durability stacks
are also untouched.
