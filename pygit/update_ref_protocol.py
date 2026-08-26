"""Input framing helpers for ``update-ref --stdin`` protocols.

The transaction engine lives in :mod:`pygit.ref_transaction`; this module keeps
byte-oriented command framing separate so NUL-delimited input never passes
through text line splitting or shell-style quoting rules.
"""

from __future__ import annotations

from typing import List

from .ref_transaction import RefUpdate
from .refs import ZERO_SHA


_CONTROLS = frozenset({"start", "prepare", "commit", "abort"})
_ACTION_FIELDS = {
    "update": 2,
    "create": 1,
    "delete": 1,
    "verify": 1,
}


def _decode_field(field: bytes, role: str) -> str:
    try:
        return field.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"update-ref -z {role} is not valid UTF-8") from exc


def parse_update_records_z(data: bytes) -> List[RefUpdate]:
    """Parse Git-style NUL-delimited ``update-ref --stdin -z`` input.

    The command/ref pair occupies the first NUL-terminated field (for example
    ``b"update refs/heads/main\\0"``). Object values then occupy their own NUL
    fields. Optional old values are represented by an empty field, not by
    omitting the field entirely. This mirrors Git's ``-z`` protocol and keeps
    whitespace literal rather than applying the line-mode tokenizer.

    Symbolic-ref transaction verbs are intentionally left to a later phase;
    direct-ref actions, transaction controls, and ``option no-deref`` compose
    with the Phase 98 transaction engine unchanged.
    """
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise ValueError("update-ref -z input must end with NUL")

    fields = data.split(b"\0")[:-1]
    updates: List[RefUpdate] = []
    index = 0

    while index < len(fields):
        header = _decode_field(fields[index], "command")
        index += 1
        if not header:
            raise ValueError("empty update-ref -z command")

        command, sep, rest = header.partition(" ")
        if command in _CONTROLS:
            if sep:
                raise ValueError(f"{command} takes no arguments")
            updates.append(RefUpdate(command))
            continue

        if command == "option":
            if not sep or rest != "no-deref":
                raise ValueError(f"unsupported update-ref option: {rest!r}")
            updates.append(RefUpdate("option", "no-deref"))
            continue

        field_count = _ACTION_FIELDS.get(command)
        if field_count is None:
            raise ValueError(f"unsupported update-ref command: {command!r}")
        if not sep or not rest:
            raise ValueError(f"malformed update-ref -z command: {header!r}")
        if index + field_count > len(fields):
            raise ValueError(
                f"{command} {rest}: unexpected end of input while reading NUL fields"
            )

        values = [
            _decode_field(fields[index + offset], "value")
            for offset in range(field_count)
        ]
        index += field_count
        refname = rest

        if command == "update":
            new_oid, old_oid = values
            # Native Git treats an empty required update value as zero (delete)
            # while an empty optional old value means "not supplied".
            updates.append(
                RefUpdate(
                    "update",
                    refname,
                    new_oid or ZERO_SHA,
                    old_oid or None,
                )
            )
        elif command == "create":
            new_oid = values[0]
            if not new_oid:
                raise ValueError("create requires a new object ID")
            updates.append(RefUpdate("create", refname, new_oid, ZERO_SHA))
        elif command == "delete":
            updates.append(RefUpdate("delete", refname, None, values[0] or None))
        else:  # verify
            updates.append(RefUpdate("verify", refname, None, values[0] or None))

    return updates
