"""Atomic smart-HTTP receive-pack support for multi-ref pushes.

Git protocol v0/v1 lets a receive-pack client send multiple ref update commands
before one packfile.  When the server advertises the ``atomic`` capability, the
client may request it on the first command so either every requested ref is
updated or none is.

This module deliberately sits beside :mod:`pygit.remote` instead of changing the
historical single-ref ``SmartHttpPushClient.push`` API.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

from .remote import (
    Advertisement,
    NativeObject,
    SmartHttpPushClient,
    build_pack,
    pkt_line,
)


_ZERO_NATIVE_OID = "0" * 40


@dataclass(frozen=True)
class AtomicRefUpdate:
    """One native receive-pack command in an atomic transaction."""

    ref_name: str
    old_oid: str
    new_oid: str


@dataclass(frozen=True)
class AtomicPushResult:
    """Result metadata for one successful atomic receive-pack request."""

    advertisement: Advertisement
    updates: Tuple[AtomicRefUpdate, ...]
    objects_sent: int


class AtomicSmartHttpPushClient(SmartHttpPushClient):
    """Send one or more ref updates in one atomic receive-pack transaction."""

    def push_many(
        self,
        updates: Sequence[Tuple[str, str]],
        objects: Dict[str, NativeObject],
        advertisement: Advertisement | None = None,
    ) -> AtomicPushResult:
        advertisement = advertisement or self.discover()
        if "atomic" not in advertisement.capabilities:
            raise RuntimeError("Remote does not support atomic pushes.")
        if not updates:
            return AtomicPushResult(advertisement, (), 0)

        normalized = []
        seen_refs = set()
        for ref_name, new_oid in updates:
            if not ref_name.startswith("refs/"):
                raise RuntimeError("atomic push requires fully-qualified destination refs")
            if ref_name in seen_refs:
                raise RuntimeError(f"atomic push contains duplicate destination ref '{ref_name}'")
            seen_refs.add(ref_name)
            if len(new_oid) != 40:
                raise RuntimeError("atomic push requires native 40-hex destination object IDs")
            old_oid = advertisement.refs.get(ref_name, _ZERO_NATIVE_OID)
            normalized.append(AtomicRefUpdate(ref_name, old_oid, new_oid))

        capabilities = []
        if "report-status" in advertisement.capabilities:
            capabilities.append("report-status")
        capabilities.append("atomic")
        if any(cap.startswith("agent=") for cap in advertisement.capabilities):
            capabilities.append("agent=pygit/0.1")

        body = b""
        for index, update in enumerate(normalized):
            suffix = f"\0{' '.join(capabilities)}" if index == 0 else ""
            body += pkt_line(
                f"{update.old_oid} {update.new_oid} {update.ref_name}{suffix}\n".encode()
            )
        body += b"0000"
        if objects:
            body += build_pack(objects.values())

        request = urllib.request.Request(
            f"{self.url}/git-receive-pack",
            data=body,
            method="POST",
            headers={
                "Accept": "application/x-git-receive-pack-result",
                "Content-Type": "application/x-git-receive-pack-request",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = response.read()
        self._check_atomic_report_status(
            result,
            tuple(update.ref_name for update in normalized),
        )
        return AtomicPushResult(
            advertisement,
            tuple(normalized),
            len(objects),
        )

    @classmethod
    def _check_atomic_report_status(
        cls,
        data: bytes,
        ref_names: Sequence[str],
    ) -> None:
        if not data:
            return
        expected = set(ref_names)
        lines = cls._status_lines(data)
        for line in lines:
            text = line.decode("utf-8", errors="replace").strip()
            if text.startswith("unpack ") and text != "unpack ok":
                raise RuntimeError(text)
            if text.startswith("ng "):
                raise RuntimeError(text)
            if text.startswith("ok "):
                ref_name = text[3:].strip()
                if ref_name and ref_name not in expected:
                    raise RuntimeError(
                        f"receive-pack reported an unexpected ref status: {ref_name}"
                    )
