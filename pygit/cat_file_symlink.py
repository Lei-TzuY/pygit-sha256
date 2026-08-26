"""Git-style ``cat-file --follow-symlinks`` batch resolution.

The ordinary revision resolver intentionally keeps its historical ``REV:path``
behavior. This module layers Git's batch-only symlink traversal and special
status records on top without changing ``rev-parse`` or other plumbing users.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Set, Tuple

from .cat_file import (
    batch_format_uses_rest,
    format_batch_object,
    parse_batch_command,
)
from .objects import BlobObject, TreeObject
from .repo import Repository
from .revision import _resolve_commit_expression, _treeish_oid, resolve_revision


_MAX_SYMLINK_HOPS = 40


@dataclass(frozen=True)
class FollowSymlinkResolution:
    """Resolved object or one Git batch protocol special result."""

    oid: Optional[str] = None
    status: Optional[str] = None
    payload: str = ""


def _special(status: str, payload: str) -> FollowSymlinkResolution:
    return FollowSymlinkResolution(status=status, payload=payload)


def resolve_follow_symlinks(
    repo: Repository,
    expression: str,
) -> FollowSymlinkResolution:
    """Resolve *expression*, following symlinks only for ``REV:path`` walks.

    Missing paths that were never reached through a symlink remain ordinary
    lookup failures and therefore produce the canonical ``<expr> missing``
    batch record. Once a symlink is followed, a missing target is reported as
    ``dangling``. Traversing through a non-directory reports ``notdir``.
    Symlinks that escape the tree report ``symlink`` plus the path outside the
    tree, and cyclic expansion reports ``loop``.
    """

    if not expression:
        raise ValueError("empty revision expression")
    if ":" not in expression:
        return FollowSymlinkResolution(oid=resolve_revision(repo, expression))

    base, path = expression.split(":", 1)
    if not base:
        raise ValueError("index-style :path expressions are not supported")

    base_oid = _resolve_commit_expression(repo, base)
    root_tree_oid = _treeish_oid(repo, base_oid, expression)
    if path == "":
        return FollowSymlinkResolution(oid=root_tree_oid)
    if path.startswith("/") or "\x00" in path:
        raise ValueError(f"Invalid object path: {expression!r}")

    components = path.split("/")
    if any(part == "" for part in components):
        raise ValueError(f"Invalid object path: {expression!r}")

    followed_symlink = False
    hops = 0
    seen_states: Set[Tuple[Tuple[str, ...], int]] = set()

    while True:
        current_tree_oid = root_tree_oid
        restart = False

        for index, part in enumerate(components):
            tree = repo.store.read(current_tree_oid)
            if not isinstance(tree, TreeObject):
                return _special("notdir", expression)

            entry = next((item for item in tree.entries if item.name == part), None)
            if entry is None:
                if followed_symlink:
                    return _special("dangling", expression)
                raise KeyError(f"Path {path!r} does not exist in {base!r}")

            remaining = components[index + 1 :]
            if entry.is_symlink:
                state = (tuple(components), index)
                if state in seen_states or hops >= _MAX_SYMLINK_HOPS:
                    return _special("loop", expression)
                seen_states.add(state)
                hops += 1

                link_obj = repo.store.read(entry.sha)
                if not isinstance(link_obj, BlobObject):
                    raise RuntimeError(
                        f"Symlink {entry.name!r} references a non-blob object"
                    )
                try:
                    target = link_obj.serialize().decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeError(
                        f"Symlink {entry.name!r} has a non-UTF-8 target"
                    ) from exc

                if target.startswith("/"):
                    suffix = "/".join(remaining)
                    outside = target if not suffix else target.rstrip("/") + "/" + suffix
                    return _special("symlink", outside)

                prefix = list(components[:index])
                target_parts = target.split("/")
                while target_parts and target_parts[0] == "..":
                    if prefix:
                        prefix.pop()
                        target_parts.pop(0)
                        continue
                    outside_parts = target_parts + list(remaining)
                    return _special("symlink", "/".join(outside_parts))

                components = prefix + target_parts + list(remaining)
                followed_symlink = True
                restart = True
                break

            if not remaining:
                return FollowSymlinkResolution(oid=entry.sha.lower())
            if not entry.is_dir:
                return _special("notdir", expression)

            child = repo.store.read(entry.sha)
            if not isinstance(child, TreeObject):
                return _special("notdir", expression)
            current_tree_oid = entry.sha.lower()

        if restart:
            continue
        raise AssertionError("unreachable")


def _special_bytes(
    resolution: FollowSymlinkResolution,
    *,
    record_terminator: bytes,
) -> bytes:
    if record_terminator not in {b"\n", b"\0"}:
        raise ValueError("record terminator must be newline or NUL")
    if resolution.status is None:
        raise ValueError("special cat-file resolution has no status")
    payload = resolution.payload.encode("utf-8")
    header = f"{resolution.status} {len(payload)}".encode("ascii")
    return header + record_terminator + payload + record_terminator


def format_batch_object_follow_symlinks(
    repo: Repository,
    expression: str,
    *,
    contents: bool = False,
    format_string: Optional[str] = None,
    rest: str = "",
    record_terminator: bytes = b"\n",
) -> bytes:
    """Format one batch record with Git's symlink-aware special statuses."""

    if record_terminator not in {b"\n", b"\0"}:
        raise ValueError("record terminator must be newline or NUL")
    if format_string is not None:
        batch_format_uses_rest(format_string)

    try:
        resolution = resolve_follow_symlinks(repo, expression)
    except (KeyError, ValueError, RuntimeError):
        return expression.encode("utf-8") + b" missing" + record_terminator

    if resolution.status is not None:
        return _special_bytes(resolution, record_terminator=record_terminator)
    assert resolution.oid is not None
    return format_batch_object(
        repo,
        resolution.oid,
        contents=contents,
        format_string=format_string,
        rest=rest,
        record_terminator=record_terminator,
    )


def run_batch_commands_follow_symlinks(
    repo: Repository,
    commands: Iterable[str],
    *,
    buffered: bool = False,
    format_string: Optional[str] = None,
    input_terminator: str = "\n",
    output_terminator: bytes = b"\n",
) -> Iterable[bytes]:
    """Execute ``--batch-command`` with symlink-aware info/contents records."""

    if input_terminator not in {"\n", "\0"}:
        raise ValueError("record terminator must be newline or NUL")
    if output_terminator not in {b"\n", b"\0"}:
        raise ValueError("record terminator must be newline or NUL")
    if format_string is not None:
        batch_format_uses_rest(format_string)

    pending = bytearray()
    for raw in commands:
        command = parse_batch_command(raw, record_terminator=input_terminator)
        if command.action == "flush":
            if not buffered:
                raise ValueError("flush is only valid with --buffer")
            yield bytes(pending)
            pending.clear()
            continue

        assert command.expression is not None
        payload = format_batch_object_follow_symlinks(
            repo,
            command.expression,
            contents=command.action == "contents",
            format_string=format_string,
            record_terminator=output_terminator,
        )
        if buffered:
            pending.extend(payload)
        else:
            yield payload

    if buffered and pending:
        yield bytes(pending)
