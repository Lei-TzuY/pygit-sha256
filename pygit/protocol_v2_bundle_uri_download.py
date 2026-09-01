"""Safe, bounded downloads for Phase379 protocol-v2 bundle URI metadata.

The bundle-uri protocol is optional acceleration.  This module resolves and
fetches one advertised URI, classifies the bytes as either a Git bundle or a
nested bundle-list config file, and stops before any object/ref mutation.
"""

from __future__ import annotations

import configparser
import hashlib
import io
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple, Union

from .protocol_v2_bundle_uri import BundleUriEntry, BundleUriList


DEFAULT_MAX_BUNDLE_URI_BYTES = 256 * 1024 * 1024
_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")
_BUNDLE_SECTION_RE = re.compile(r'^bundle "([A-Za-z0-9-]+)"$', re.IGNORECASE)
_NATIVE_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_CAPABILITY_KEY_BYTES = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
)
_MAX_CREATION_TOKEN = (1 << 64) - 1


@dataclass(frozen=True)
class BundleFileHeader:
    version: int
    capabilities: Tuple[Tuple[str, Optional[str]], ...]
    prerequisites: Tuple[str, ...]
    references: Tuple[Tuple[str, str], ...]
    pack_version: int
    object_count: int
    pack_sha1: str


@dataclass(frozen=True)
class DownloadedBundleFile:
    uri: str
    payload: bytes
    header: BundleFileHeader


@dataclass(frozen=True)
class DownloadedBundleList:
    uri: str
    payload: bytes
    bundle_list: BundleUriList


DownloadedBundleUriPayload = Union[DownloadedBundleFile, DownloadedBundleList]


def _validate_http_uri(uri: str, *, context: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(uri)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"{context} must use HTTP or HTTPS")
    if not parsed.hostname:
        raise ValueError(f"{context} is missing a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{context} must not contain embedded credentials")
    if parsed.fragment:
        raise ValueError(f"{context} must not contain a URI fragment")
    return parsed


def resolve_bundle_uri(base_uri: str, advertised_uri: str) -> str:
    """Resolve one bundle-list URI using Git's documented base-URI model."""

    _validate_http_uri(base_uri, context="bundle-list base URI")
    if (
        not advertised_uri
        or "\x00" in advertised_uri
        or "\r" in advertised_uri
        or "\n" in advertised_uri
    ):
        raise ValueError("bundle URI contains an invalid character")

    advertised = urllib.parse.urlsplit(advertised_uri)
    if advertised.scheme:
        resolved = advertised_uri
    else:
        # A scheme-relative URL can silently replace the authority. The bundle
        # protocol documents absolute HTTP(S) URLs and path-relative URLs, so
        # require an explicit scheme when changing hosts.
        if advertised_uri.startswith("//"):
            raise ValueError("bundle URI must use an explicit scheme when changing hosts")
        resolved = urllib.parse.urljoin(base_uri, advertised_uri)

    parsed = _validate_http_uri(resolved, context="bundle URI")
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )


class _SafeBundleRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirect scheme escapes and HTTPS-to-HTTP downgrade redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = _validate_http_uri(newurl, context="bundle URI redirect")
        if old.scheme.lower() == "https" and new.scheme.lower() != "https":
            raise ValueError("bundle URI redirect must not downgrade HTTPS to HTTP")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _response_content_length(response) -> Optional[int]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw = getter("Content-Length")
    if raw is None:
        return None
    try:
        value = int(str(raw), 10)
    except ValueError as exc:
        raise ValueError("invalid bundle URI Content-Length") from exc
    if value < 0:
        raise ValueError("invalid bundle URI Content-Length")
    return value


def _read_bounded(response, max_bytes: int) -> bytes:
    advertised = _response_content_length(response)
    if advertised is not None and advertised > max_bytes:
        raise ValueError("bundle URI payload exceeds configured size limit")

    chunks = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("bundle URI payload exceeds configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_bundle_capability(line: bytes) -> Tuple[str, Optional[str]]:
    raw = line[1:]
    key, separator, value = raw.partition(b"=")
    if not key or any(byte not in _CAPABILITY_KEY_BYTES for byte in key):
        raise ValueError("invalid Git bundle capability")
    try:
        key_text = key.decode("ascii")
        value_text = value.decode("utf-8") if separator else None
    except UnicodeDecodeError as exc:
        raise ValueError("invalid Git bundle capability encoding") from exc
    if key_text not in {"object-format", "filter"}:
        raise ValueError(f"unsupported Git bundle capability: {key_text}")
    return key_text, value_text


def _validate_native_sha1(raw: bytes, *, context: str) -> str:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid {context} object id") from exc
    if not _NATIVE_SHA1_RE.fullmatch(text):
        raise ValueError(f"invalid {context} object id")
    return text.lower()


def _valid_bundle_refname(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid Git bundle refname encoding") from exc
    if text == "HEAD":
        return text
    if (
        not text.startswith("refs/")
        or text.endswith("/")
        or ".." in text
        or "//" in text
        or "\\" in text
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text)
    ):
        raise ValueError("invalid Git bundle refname")
    return text


def parse_bundle_file(payload: bytes) -> BundleFileHeader:
    """Validate one SHA-1 Git bundle header plus its pack checksum.

    Thin bundles are allowed: this phase validates the envelope/header and pack
    checksum only and deliberately does not attempt object connectivity/import.
    """

    separator = payload.find(b"\n\n")
    if separator < 0:
        raise ValueError("Git bundle header is missing its terminating blank line")
    header_bytes = payload[:separator]
    pack = payload[separator + 2 :]
    lines = header_bytes.split(b"\n")
    if not lines:
        raise ValueError("empty Git bundle header")

    signature = lines[0]
    if signature == b"# v2 git bundle":
        version = 2
    elif signature == b"# v3 git bundle":
        version = 3
    else:
        raise ValueError("unsupported Git bundle signature")

    capabilities = []
    prerequisites = []
    references = []
    seen_capabilities = set()
    saw_object_record = False

    for line in lines[1:]:
        if not line or b"\r" in line or b"\x00" in line:
            raise ValueError("malformed Git bundle header record")
        if line.startswith(b"@"):
            if version != 3 or saw_object_record:
                raise ValueError("misplaced Git bundle capability")
            key, value = _parse_bundle_capability(line)
            if key in seen_capabilities:
                raise ValueError(f"duplicate Git bundle capability: {key}")
            seen_capabilities.add(key)
            capabilities.append((key, value))
            continue

        saw_object_record = True
        if line.startswith(b"-"):
            oid_raw, separator_byte, _comment = line[1:].partition(b" ")
            if not separator_byte:
                raise ValueError("malformed Git bundle prerequisite")
            prerequisites.append(
                _validate_native_sha1(oid_raw, context="bundle prerequisite")
            )
            continue

        oid_raw, separator_byte, ref_raw = line.partition(b" ")
        if not separator_byte or not ref_raw:
            raise ValueError("malformed Git bundle reference")
        oid = _validate_native_sha1(oid_raw, context="bundle reference")
        refname = _valid_bundle_refname(ref_raw)
        if any(existing == refname for existing, _oid in references):
            raise ValueError(f"duplicate Git bundle reference: {refname}")
        references.append((refname, oid))

    capability_map = dict(capabilities)
    object_format = capability_map.get("object-format")
    if object_format not in (None, "sha1"):
        raise ValueError(
            f"unsupported Git bundle object format: {object_format}; expected sha1"
        )
    if not references:
        raise ValueError("Git bundle contains no reference tips")

    if len(pack) < 12 + 20 or not pack.startswith(b"PACK"):
        raise ValueError("Git bundle does not contain a valid PACK payload")
    pack_version = int.from_bytes(pack[4:8], "big")
    if pack_version not in (2, 3):
        raise ValueError("unsupported Git bundle pack version")
    object_count = int.from_bytes(pack[8:12], "big")
    trailer = pack[-20:]
    if hashlib.sha1(pack[:-20]).digest() != trailer:
        raise ValueError("Git bundle PACK checksum mismatch")

    return BundleFileHeader(
        version=version,
        capabilities=tuple(capabilities),
        prerequisites=tuple(prerequisites),
        references=tuple(references),
        pack_version=pack_version,
        object_count=object_count,
        pack_sha1=trailer.hex(),
    )


def _config_parser() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(
        interpolation=None,
        strict=True,
        delimiters=("=",),
        comment_prefixes=("#", ";"),
        inline_comment_prefixes=None,
        empty_lines_in_values=False,
    )
    parser.optionxform = str.lower
    return parser


def _parse_uint64(value: str) -> Optional[int]:
    if not value.isdigit():
        return None
    token = int(value, 10)
    if token > _MAX_CREATION_TOKEN:
        return None
    return token


def parse_bundle_list_config(payload: bytes, *, base_uri: str) -> Optional[BundleUriList]:
    """Parse a downloaded Git-config bundle list and resolve its child URIs."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in text:
        return None

    parser = _config_parser()
    try:
        parser.read_file(io.StringIO(text))
    except (configparser.Error, ValueError):
        return None

    global_section = next(
        (section for section in parser.sections() if section.lower() == "bundle"),
        None,
    )
    if global_section is None:
        return None
    try:
        version = parser.get(global_section, "version")
        mode = parser.get(global_section, "mode")
    except (configparser.NoOptionError, configparser.NoSectionError):
        return None
    if version != "1" or mode.lower() not in {"all", "any"}:
        return None

    heuristic_raw = parser.get(global_section, "heuristic", fallback=None)
    heuristic = "creationToken" if heuristic_raw == "creationToken" else None
    bundles = []

    for section in parser.sections():
        if section == global_section:
            continue
        match = _BUNDLE_SECTION_RE.fullmatch(section)
        if match is None:
            continue
        bundle_id = match.group(1)
        uri = parser.get(section, "uri", fallback=None)
        if not uri:
            return None
        try:
            resolved = resolve_bundle_uri(base_uri, uri)
        except ValueError:
            return None
        token_raw = parser.get(section, "creationtoken", fallback=None)
        bundles.append(
            BundleUriEntry(
                bundle_id=bundle_id,
                uri=resolved,
                filter_spec=parser.get(section, "filter", fallback=None),
                creation_token=(
                    _parse_uint64(token_raw) if token_raw is not None else None
                ),
                location=parser.get(section, "location", fallback=None),
            )
        )

    if not bundles:
        return None
    return BundleUriList(
        version=1,
        mode=mode.lower(),
        heuristic=heuristic,
        bundles=tuple(sorted(bundles, key=lambda item: item.bundle_id)),
    )


def classify_bundle_uri_payload(
    payload: bytes,
    *,
    source_uri: str,
) -> DownloadedBundleUriPayload:
    if payload.startswith((b"# v2 git bundle\n", b"# v3 git bundle\n")):
        return DownloadedBundleFile(
            uri=source_uri,
            payload=payload,
            header=parse_bundle_file(payload),
        )

    nested = parse_bundle_list_config(payload, base_uri=source_uri)
    if nested is None:
        raise ValueError("bundle URI payload is neither a valid Git bundle nor bundle list")
    return DownloadedBundleList(uri=source_uri, payload=payload, bundle_list=nested)


def download_bundle_uri_payload(
    base_uri: str,
    entry: BundleUriEntry,
    *,
    timeout: int = 30,
    max_bytes: int = DEFAULT_MAX_BUNDLE_URI_BYTES,
    opener=None,
) -> DownloadedBundleUriPayload:
    """Download and validate one advertised bundle URI without repository writes."""

    if timeout <= 0:
        raise ValueError("bundle URI timeout must be positive")
    if max_bytes <= 0:
        raise ValueError("bundle URI maximum size must be positive")

    resolved = resolve_bundle_uri(base_uri, entry.uri)
    request = urllib.request.Request(
        resolved,
        method="GET",
        headers={"Accept": "application/octet-stream, text/plain;q=0.9, */*;q=0.1"},
    )
    transport = opener or urllib.request.build_opener(_SafeBundleRedirectHandler())
    with transport.open(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise ValueError(f"bundle URI returned unexpected HTTP status {status}")
        final_url = (
            response.geturl()
            if callable(getattr(response, "geturl", None))
            else resolved
        )
        _validate_http_uri(final_url, context="bundle URI response URL")
        payload = _read_bounded(response, max_bytes)
    return classify_bundle_uri_payload(payload, source_uri=final_url)


def try_download_bundle_uri_payload(
    base_uri: str,
    entry: BundleUriEntry,
    *,
    timeout: int = 30,
    max_bytes: int = DEFAULT_MAX_BUNDLE_URI_BYTES,
    opener=None,
) -> Optional[DownloadedBundleUriPayload]:
    """Best-effort bundle acceleration: return ``None`` on remote/payload errors."""

    try:
        return download_bundle_uri_payload(
            base_uri,
            entry,
            timeout=timeout,
            max_bytes=max_bytes,
            opener=opener,
        )
    except (OSError, ValueError):
        return None
