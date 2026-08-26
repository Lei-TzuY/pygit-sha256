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
_SYMREF_ACTIONS = frozenset({"symref-update", "symref-create", "symref-delete", "symref-verify"})


def _decode_field(field: bytes, role: str) -> str:
    try:
        return field.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"update-ref -z {role} is not valid UTF-8") from exc


def _need_fields(fields: List[bytes], index: int, count: int, label: str) -> None:
    if index + count > len(fields):
        raise ValueError(f"{label}: unexpected end of input while reading NUL fields")


def parse_update_records_z(data: bytes) -> List[RefUpdate]:
    """Parse Git-style NUL-delimited ``update-ref --stdin -z`` input.

    The command/ref pair occupies the first NUL-terminated field. Required and
    optional values then occupy separate fields; an empty optional field means
    "not supplied". Symbolic-ref commands use the same transaction records as
    line mode, including the variable ``symref-update`` old-value discriminator.
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

        if not sep or not rest:
            raise ValueError(f"malformed update-ref -z command: {header!r}")
        refname = rest

        field_count = _ACTION_FIELDS.get(command)
        if field_count is not None:
            _need_fields(fields, index, field_count, f"{command} {refname}")
            values = [
                _decode_field(fields[index + offset], "value")
                for offset in range(field_count)
            ]
            index += field_count

            if command == "update":
                new_oid, old_oid = values
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
            else:
                updates.append(RefUpdate("verify", refname, None, values[0] or None))
            continue

        if command not in _SYMREF_ACTIONS:
            raise ValueError(f"unsupported update-ref command: {command!r}")

        if command in {"symref-create", "symref-delete", "symref-verify"}:
            _need_fields(fields, index, 1, f"{command} {refname}")
            value = _decode_field(fields[index], "symbolic-ref value")
            index += 1
            if command == "symref-create":
                if not value:
                    raise ValueError("symref-create requires a new target")
                updates.append(RefUpdate(command, refname=refname, new_target=value))
            else:
                updates.append(RefUpdate(command, refname=refname, old_target=value or None))
            continue

        # symref-update: new-target is required. The optional old-value clause
        # starts with a literal discriminator field ("ref" or "oid"). Since no
        # command header can be one of those bare words, peeking is unambiguous.
        _need_fields(fields, index, 1, f"symref-update {refname}")
        new_target = _decode_field(fields[index], "symbolic-ref target")
        index += 1
        if not new_target:
            raise ValueError("symref-update requires a new target")

        old_kind = None
        old_target = None
        old_oid = None
        if index < len(fields) and fields[index] in {b"ref", b"oid"}:
            old_kind = _decode_field(fields[index], "symbolic-ref old-value kind")
            index += 1
            _need_fields(fields, index, 1, f"symref-update {refname}")
            old_value = _decode_field(fields[index], "symbolic-ref old value")
            index += 1
            if not old_value:
                raise ValueError("symref-update old-value condition cannot be empty")
            if old_kind == "ref":
                old_target = old_value
            else:
                old_oid = old_value

        updates.append(
            RefUpdate(
                "symref-update",
                refname=refname,
                old_oid=old_oid,
                new_target=new_target,
                old_target=old_target,
                old_kind=old_kind,
            )
        )

    return updates
