"""Persistent promised-object metadata for filtered protocol-v2 fetches.

A partial fetch can intentionally omit native Git objects.  pygit must keep
those omissions distinct from repository corruption: this module records the
native SHA-1 identities that a promisor remote has promised, plus native-to-
local SHA-256 resolutions that become available later.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional


_STATE_FILE = "promisor.json"


class PromisorMissingError(KeyError):
    """Raised when an operation requires an intentionally omitted object."""

    def __init__(self, native_oid: str, kind: str = "object") -> None:
        self.native_oid = native_oid
        self.kind = kind
        super().__init__(
            f"promisor {kind} {native_oid} is not materialized; fetch it from the promisor remote"
        )


def _path(pygit_dir: Path) -> Path:
    return Path(pygit_dir) / _STATE_FILE


def read_promisor_state(pygit_dir: Path) -> dict:
    path = _path(pygit_dir)
    if not path.is_file():
        return {"version": 1, "remotes": {}, "promised": {}, "resolved": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError("unsupported promisor state version")
    data.setdefault("remotes", {})
    data.setdefault("promised", {})
    data.setdefault("resolved", {})
    return data


def write_promisor_state(pygit_dir: Path, state: Mapping[str, object]) -> None:
    path = _path(pygit_dir)
    payload = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
    fd, name = tempfile.mkstemp(prefix="promisor-", suffix=".lock", dir=str(path.parent))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def update_promisor_state(
    pygit_dir: Path,
    *,
    remote: Optional[str] = None,
    filter_spec: Optional[str] = None,
    promised: Optional[Mapping[str, str]] = None,
    resolved: Optional[Mapping[str, str]] = None,
) -> None:
    state = read_promisor_state(pygit_dir)
    if remote is not None:
        state["remotes"][remote] = {"filter": filter_spec or ""}
    if promised:
        for native_oid, kind in promised.items():
            if native_oid not in state["resolved"]:
                state["promised"][native_oid] = kind
    if resolved:
        for native_oid, local_oid in resolved.items():
            state["resolved"][native_oid] = local_oid
            state["promised"].pop(native_oid, None)
    write_promisor_state(pygit_dir, state)


def resolved_native_objects(pygit_dir: Path) -> Dict[str, str]:
    return dict(read_promisor_state(pygit_dir)["resolved"])


def promised_kind(pygit_dir: Path, native_oid: str) -> Optional[str]:
    value = read_promisor_state(pygit_dir)["promised"].get(native_oid)
    return str(value) if value is not None else None


def is_promisor_repository(pygit_dir: Path) -> bool:
    state = read_promisor_state(pygit_dir)
    return bool(state["remotes"] or state["promised"])
