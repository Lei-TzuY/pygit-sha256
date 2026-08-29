"""Promisor-aware batching for buffered ``cat-file --batch-command``.

Phase230 uses Git's explicit ``flush`` boundary as an object-demand batching
boundary.  A buffered command group may name several foreign ``REV:path`` blobs;
without planning, each final tree entry faults in independently while the
historical cat-file formatter resolves and reads it.

This module does not replace cat-file parsing or rendering.  It wraps the
existing ``run_batch_commands`` function, predicts only unresolved final blob
promises that can be discovered without touching their ``TreeEntry.sha``
property, materializes the deduplicated set once, and then delegates the group
back to the historical implementation.
"""

from __future__ import annotations

import re
from functools import wraps
from typing import Iterable, Optional, Set

from .objects import TreeObject
from .promisor import promised_kind, read_promisor_state
from .promisor_materialize import materialize_promised_objects


_INSTALLED = False
_PEEL_RE = re.compile(r"^(.*)\^\{([^{}]*)\}$", re.DOTALL)


def _tree_path_expression(expression: str):
    """Return ``(base, path)`` for a prefetch-safe ``REV:path`` expression.

    Explicit peel selectors that could only succeed for a blob/object are safe
    to plan.  Other selectors are left to the historical resolver without a
    speculative fetch, preserving type-error-before-network behavior.
    """
    current = expression
    while True:
        match = _PEEL_RE.fullmatch(current)
        if match is None:
            break
        current, selector = match.groups()
        if selector not in {"", "object", "blob"}:
            return None

    if current.startswith(":") or ":" not in current:
        return None
    base, path = current.split(":", 1)
    if not base or not path:
        return None
    if path.startswith("/") or "\x00" in path:
        return None
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return base, parts


def _expression_promise(repo, expression: str) -> Optional[str]:
    """Discover one unresolved final blob promise without resolving its SHA."""
    parsed = _tree_path_expression(expression)
    if parsed is None:
        return None
    base, parts = parsed

    # Import private revision primitives locally.  They are exactly the metadata
    # resolution steps used by resolve_revision(), but stop before the final
    # unresolved blob entry's ``sha`` property can trigger lazy materialization.
    from .revision import _resolve_commit_expression, _treeish_oid

    try:
        base_oid = _resolve_commit_expression(repo, base)
        current_oid = _treeish_oid(repo, base_oid, expression)

        for index, part in enumerate(parts):
            tree = repo.store.read(current_oid)
            if not isinstance(tree, TreeObject):
                return None
            entry = next((item for item in tree.entries if item.name == part), None)
            if entry is None:
                return None

            final = index == len(parts) - 1
            if final:
                if entry.is_dir or entry.is_resolved or not entry.native_oid:
                    return None
                if promised_kind(repo.pygit_dir, entry.native_oid):
                    return entry.native_oid
                return None

            if not entry.is_dir:
                return None
            # Blob filters retain the tree graph.  If a future filter leaves an
            # intermediate tree unresolved, do not turn planning itself into a
            # demand fetch; the historical resolver remains authoritative.
            if not entry.is_resolved:
                return None
            current_oid = entry.sha
    except (KeyError, ValueError, RuntimeError):
        return None

    return None


def collect_cat_file_promises(repo, expressions: Iterable[str]) -> Set[str]:
    """Return the deduplicated unresolved ``REV:path`` blob promises."""
    state = read_promisor_state(repo.pygit_dir)
    if not state.get("promised"):
        return set()

    promises: Set[str] = set()
    for expression in expressions:
        oid = _expression_promise(repo, expression)
        if oid:
            promises.add(oid)
    return promises


def prefetch_cat_file_promises(repo, expressions: Iterable[str]) -> Set[str]:
    """Materialize one buffered cat-file group's predictable object demand."""
    promises = collect_cat_file_promises(repo, expressions)
    if promises:
        materialize_promised_objects(repo.pygit_dir, sorted(promises))
    return promises


def install_promisor_cat_file_support() -> None:
    """Wrap buffered batch-command execution with flush-group prefetching."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import cat_file

    original = cat_file.run_batch_commands

    @wraps(original)
    def run_batch_commands(
        repo,
        commands,
        *,
        buffered: bool = False,
        format_string=None,
        input_terminator: str = "\n",
        output_terminator: bytes = b"\n",
    ):
        if not buffered:
            yield from original(
                repo,
                commands,
                buffered=False,
                format_string=format_string,
                input_terminator=input_terminator,
                output_terminator=output_terminator,
            )
            return

        group = []
        expressions = []
        saw_input = False

        def execute_group(include_flush: bool):
            if expressions:
                prefetch_cat_file_promises(repo, expressions)
            payloads = original(
                repo,
                tuple(group),
                buffered=True,
                format_string=format_string,
                input_terminator=input_terminator,
                output_terminator=output_terminator,
            )
            yield from payloads

        for raw in commands:
            saw_input = True
            command = cat_file.parse_batch_command(
                raw,
                record_terminator=input_terminator,
            )
            group.append(raw)
            if command.action == "flush":
                yield from execute_group(include_flush=True)
                group.clear()
                expressions.clear()
                continue
            if command.expression is not None:
                expressions.append(command.expression)

        if group:
            yield from execute_group(include_flush=False)
        elif not saw_input:
            # Preserve the historical eager validation of protocol terminators
            # and custom formats even when stdin is empty.
            yield from original(
                repo,
                (),
                buffered=True,
                format_string=format_string,
                input_terminator=input_terminator,
                output_terminator=output_terminator,
            )

    cat_file.run_batch_commands = run_batch_commands
    _INSTALLED = True
