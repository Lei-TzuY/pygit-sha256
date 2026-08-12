"""
pygit/signature.py
==================
Commit Signature Inspector and Verifier
=======================================

Parses embedded OpenPGP gpgsig multiline headers in CommitObject payloads.
"""

from __future__ import annotations

from typing import Optional, Tuple


class CommitSignatureInfo:
    """Signature metadata extracted from a raw commit object."""

    def __init__(self, sha: str, has_signature: bool, signature_block: Optional[str], signed_payload: bytes) -> None:
        self.sha = sha
        self.has_signature = has_signature
        self.signature_block = signature_block
        self.signed_payload = signed_payload


def parse_commit_signature(sha: str, raw_bytes: bytes) -> CommitSignatureInfo:
    """
    Extract gpgsig header lines and return CommitSignatureInfo.
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return CommitSignatureInfo(sha, False, None, raw_bytes)

    lines = text.splitlines(True)
    header_lines = []
    sig_lines = []
    in_sig = False

    for line in lines:
        if line.startswith("gpgsig "):
            in_sig = True
            sig_lines.append(line[7:])
        elif in_sig and line.startswith(" "):
            sig_lines.append(line[1:])
        else:
            in_sig = False
            header_lines.append(line)

    if not sig_lines:
        return CommitSignatureInfo(sha, False, None, raw_bytes)

    sig_block = "".join(sig_lines).strip()
    signed_payload = "".join(header_lines).encode("utf-8")
    return CommitSignatureInfo(sha, True, sig_block, signed_payload)
