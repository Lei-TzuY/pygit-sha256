"""Strict annotated-tag object creation plumbing.

``mktag`` differs from generic ``hash-object -t tag`` in one important way:
it validates both the tag payload and its relationship to the local object
store before writing the exact input bytes as a tag object.
"""

from __future__ import annotations

import re
from typing import Tuple

from .hash_object import write_object_data
from .objects import Identity, TagObject
from .ref_query import check_ref_format
from .repo import Repository


_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TZ_RE = re.compile(r"^[+-](\d{2})(\d{2})$")
_ALLOWED_TYPES = {"blob", "tree", "commit", "tag"}


def _parse_identity(value: str) -> Identity:
    try:
        identity = Identity.decode(value)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"invalid tagger identity: {exc}") from exc

    if not identity.name:
        raise ValueError("tagger name must not be empty")
    if not identity.email or any(ch in identity.email for ch in "<>\n\r"):
        raise ValueError("tagger email is invalid")
    if identity.timestamp < 0:
        raise ValueError("tagger timestamp must be non-negative")

    match = _TZ_RE.fullmatch(identity.timezone)
    if match is None:
        raise ValueError("tagger timezone must use [+|-]HHMM format")
    hours, minutes = (int(part) for part in match.groups())
    if hours > 23 or minutes > 59:
        raise ValueError("tagger timezone is out of range")

    # Reject surprising strings accepted by the permissive Identity decoder.
    if identity.encode() != value:
        raise ValueError("tagger identity is not in canonical form")
    return identity


def parse_tag_payload(payload: bytes) -> Tuple[TagObject, str]:
    """Parse and structurally validate a canonical annotated-tag payload."""
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("tag payload must be valid UTF-8") from exc

    if "\r" in text:
        raise ValueError("tag payload must use LF line endings")
    if "\n\n" not in text:
        raise ValueError("tag payload is missing the header/message separator")

    header_block, message = text.split("\n\n", 1)
    lines = header_block.split("\n")
    if len(lines) != 4:
        raise ValueError("tag payload must contain exactly four canonical headers")

    expected = ("object ", "type ", "tag ", "tagger ")
    values = []
    for line, prefix in zip(lines, expected):
        if not line.startswith(prefix):
            raise ValueError(f"expected {prefix.strip()!r} tag header")
        value = line[len(prefix) :]
        if not value:
            raise ValueError(f"{prefix.strip()} tag header must not be empty")
        values.append(value)

    target_sha, target_type, tag_name, tagger_value = values
    if _HEX_RE.fullmatch(target_sha) is None:
        raise ValueError("tag object header must contain a 64-hex object ID")
    if target_type not in _ALLOWED_TYPES:
        raise ValueError(f"unsupported tag target type: {target_type!r}")

    # A tag object's name should be valid when placed below refs/tags/ even
    # though mktag itself intentionally does not create that ref.
    check_ref_format(f"refs/tags/{tag_name}")
    tagger = _parse_identity(tagger_value)

    return (
        TagObject(
            target_sha=target_sha.lower(),
            target_type=target_type.encode("ascii"),
            tag_name=tag_name,
            tagger=tagger,
            message=message,
        ),
        target_sha.lower(),
    )


def validate_tag_payload(repo: Repository, payload: bytes) -> TagObject:
    """Validate tag syntax, target existence, and declared target type."""
    tag, target_sha = parse_tag_payload(payload)
    try:
        target = repo.store.read(target_sha)
    except KeyError as exc:
        raise ValueError(f"tag target object does not exist: {target_sha}") from exc

    actual_type = target.type_name.decode("ascii", "strict")
    declared_type = tag.target_type.decode("ascii", "strict")
    if actual_type != declared_type:
        raise ValueError(
            f"tag target type mismatch: payload says {declared_type}, object is {actual_type}"
        )
    return tag


def make_tag(repo: Repository, payload: bytes) -> str:
    """Validate and write the exact annotated-tag payload, returning its SHA-256 ID."""
    validate_tag_payload(repo, payload)
    return write_object_data(repo, payload, "tag")
