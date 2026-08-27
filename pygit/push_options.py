"""Git-style push-option selection and receive-pack framing.

Push options are a protocol-v0/v1 capability layered between the ref update
command flush and the packfile.  Existing push clients remain unchanged; this
module provides opt-in clients used only when at least one option is active.
"""

from __future__ import annotations

import urllib.request
from typing import Dict, Optional, Sequence, Tuple

from .config import GitConfig
from .push_atomic import AtomicPushResult, AtomicRefUpdate, AtomicSmartHttpPushClient
from .remote import (
    Advertisement,
    NativeObject,
    PushResult,
    SmartHttpPushClient,
    build_pack,
    pkt_line,
)
from .repo import Repository


_ZERO_NATIVE_OID = "0" * 40


def validate_push_options(options: Sequence[str]) -> Tuple[str, ...]:
    """Validate push-option payloads and preserve their order exactly."""
    normalized = tuple(str(option) for option in options)
    for option in normalized:
        if "\x00" in option:
            raise RuntimeError("push options must not contain NUL characters")
        if "\n" in option:
            raise RuntimeError("push options must not have new line characters")
    return normalized


def resolve_push_options(
    repo: Repository,
    command_line: Optional[Sequence[str]],
) -> Tuple[str, ...]:
    """Resolve CLI push options over multi-valued ``push.pushOption`` config.

    Any command-line occurrence, including an explicitly empty option, replaces
    the configured list.  Configuration is consulted only when the command line
    did not provide a push-option argument at all.
    """
    if command_line is not None:
        return validate_push_options(command_line)
    configured = GitConfig(repo.pygit_dir).get_all("push", "pushOption")
    return validate_push_options(configured)


def require_push_options_capability(
    advertisement: Advertisement,
    push_options: Sequence[str],
) -> None:
    """Fail when options were requested but receive-pack cannot accept them."""
    if push_options and "push-options" not in advertisement.capabilities:
        raise RuntimeError("Remote does not support push options.")


def _push_option_packets(options: Sequence[str]) -> bytes:
    body = b"".join(pkt_line(option.encode("utf-8")) for option in options)
    return body + b"0000"


class PushOptionSmartHttpPushClient(SmartHttpPushClient):
    """Single-ref receive-pack client that transmits push options."""

    def push_with_options(
        self,
        ref_name: str,
        new_oid: str,
        objects: Dict[str, NativeObject],
        push_options: Sequence[str],
        advertisement: Optional[Advertisement] = None,
    ) -> PushResult:
        options = validate_push_options(push_options)
        advertisement = advertisement or self.discover()
        require_push_options_capability(advertisement, options)
        old_oid = advertisement.refs.get(ref_name, _ZERO_NATIVE_OID)

        capabilities = []
        if "report-status" in advertisement.capabilities:
            capabilities.append("report-status")
        capabilities.append("push-options")
        if any(cap.startswith("agent=") for cap in advertisement.capabilities):
            capabilities.append("agent=pygit/0.1")

        suffix = f"\0{' '.join(capabilities)}"
        body = pkt_line(f"{old_oid} {new_oid} {ref_name}{suffix}\n".encode())
        body += b"0000"
        body += _push_option_packets(options)
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
        self._check_report_status(result, ref_name)
        return PushResult(advertisement, ref_name, old_oid, new_oid, len(objects))


class PushOptionAtomicSmartHttpPushClient(AtomicSmartHttpPushClient):
    """Atomic multi-ref receive-pack client that transmits push options."""

    def push_many_with_options(
        self,
        updates: Sequence[Tuple[str, str]],
        objects: Dict[str, NativeObject],
        push_options: Sequence[str],
        advertisement: Optional[Advertisement] = None,
    ) -> AtomicPushResult:
        options = validate_push_options(push_options)
        advertisement = advertisement or self.discover()
        if "atomic" not in advertisement.capabilities:
            raise RuntimeError("Remote does not support atomic pushes.")
        require_push_options_capability(advertisement, options)
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
        capabilities.extend(("atomic", "push-options"))
        if any(cap.startswith("agent=") for cap in advertisement.capabilities):
            capabilities.append("agent=pygit/0.1")

        body = b""
        for index, update in enumerate(normalized):
            suffix = f"\0{' '.join(capabilities)}" if index == 0 else ""
            body += pkt_line(
                f"{update.old_oid} {update.new_oid} {update.ref_name}{suffix}\n".encode()
            )
        body += b"0000"
        body += _push_option_packets(options)
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
        return AtomicPushResult(advertisement, tuple(normalized), len(objects))
