"""Git-compatible ``rev-list --filter-print-omitted`` presentation.

The object-filter adapter already performs metadata-only filtering without
materializing promisor objects. This adapter adds the complementary ``~<oid>``
omission channel for local SHA-256 objects while keeping unresolved foreign
promises out of the repository-visible hash domain.

Phase254 also composes the omission channel with ``--count``. Git emits normal
traversal records first, then omitted records, then missing-object diagnostics,
and finally the count. We preserve that ordering instead of treating the count
as ordinary filter output.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_filter_cli as _filter
from . import rev_list_promisor_cli as _promisor


_FILTER_PRINT_OMITTED = "--filter-print-omitted"
_DEFERRED_WITH_OMITTED = {"-z", "--boundary", "--objects-edge"}


def _omitted_local_oids(repo, argv: Sequence[str], *, spec: str) -> tuple[str, ...]:
    """Return local SHA-256 objects omitted by one supported object filter.

    Git's textual omitted-object channel is an object-id channel, not an
    arbitrary transport-id channel. pygit therefore refuses a request when an
    unresolved promise itself would have to be reported as omitted: before
    materialization there is no genuine local SHA-256 object id to print.
    """

    if spec.startswith("object:type="):
        projected = _filter._project_object_type(argv)
        requested = spec.split("=", 1)[1]
    else:
        projected = _filter._project(argv)
        requested = None

    parsed = _filter._parse_inventory_request(projected)
    entries = _promisor.promisor_object_inventory(
        repo,
        parsed["revisions"],
        all_refs=parsed["all_refs"],
        first_parent=parsed["first_parent"],
        topo_order=parsed["topo_order"],
        reverse=parsed["reverse"],
        skip=parsed["skip"],
        max_count=parsed["max_count"],
    )

    provided = frozenset()
    if spec.startswith("object:type=") and "--filter-provided-objects" not in argv:
        provided = _filter._provided_commit_roots(repo, parsed)

    omitted: list[str] = []
    for entry in entries:
        if spec == "blob:none":
            should_omit = entry.type_name == "blob"
        else:
            assert requested is not None
            should_omit = entry.type_name != requested
            if (
                should_omit
                and entry.type_name == "commit"
                and entry.path is None
                and entry.oid is not None
                and entry.oid.lower() in provided
            ):
                should_omit = False

        if not should_omit:
            continue
        if entry.oid is None:
            native = entry.native_oid or "<unknown>"
            raise ValueError(
                "--filter-print-omitted cannot expose unresolved promisor object "
                f"{native} as a local SHA-256 id; materialize it first or omit "
                "--filter-print-omitted"
            )
        oid = entry.oid.lower()
        if len(oid) != 64 or any(ch not in "0123456789abcdef" for ch in oid):
            raise RuntimeError("omitted local object has no valid SHA-256 identity")
        omitted.append(oid)

    return tuple(omitted)


def _partition_projected_lines(
    lines: Sequence[str], *, count_mode: bool
) -> tuple[tuple[str, ...], tuple[str, ...], Optional[str]]:
    """Split projected output into traversal, missing, and final count records.

    The underlying metadata-only filter path already computes the correct
    filtered integer. Git's rev-list source emits omitted objects after the
    traversal but before missing-object diagnostics and the final ``--count``
    line, so this helper only rearranges presentation; it does not recompute
    selection or count semantics.
    """

    projected = list(lines)
    count_line: Optional[str] = None
    if count_mode:
        if not projected:
            raise RuntimeError("rev-list omitted/count projection produced no output")
        count_line = projected.pop()
        try:
            int(count_line)
        except ValueError as exc:
            raise RuntimeError(
                "rev-list omitted/count projection did not end with an integer"
            ) from exc

    traversal: list[str] = []
    missing: list[str] = []
    for line in projected:
        if line.startswith("?"):
            missing.append(line)
        else:
            traversal.append(line)
    return tuple(traversal), tuple(missing), count_line


def try_run_rev_list_filter_print_omitted(argv: Sequence[str]) -> Optional[int]:
    """Handle the line-oriented local-SHA-256 omitted-object protocol."""

    if _FILTER_PRINT_OMITTED not in argv:
        return None
    if argv.count(_FILTER_PRINT_OMITTED) != 1:
        raise ValueError("rev-list accepts --filter-print-omitted at most once")

    cleaned = [arg for arg in argv if arg != _FILTER_PRINT_OMITTED]
    spec = _filter._filter_spec(cleaned)
    if spec is None:
        raise ValueError("--filter-print-omitted requires --filter")

    for option in _DEFERRED_WITH_OMITTED:
        if option in cleaned:
            raise ValueError(
                f"{option} is not yet supported with --filter-print-omitted"
            )

    repo = _promisor._find_repo()
    omitted = _omitted_local_oids(repo, cleaned, spec=spec)

    capture = io.StringIO()
    with redirect_stdout(capture):
        code = _filter.try_run_rev_list_filter(cleaned)
    if code is None:
        raise RuntimeError("rev-list filter adapter declined omitted-object projection")

    traversal, missing, count_line = _partition_projected_lines(
        capture.getvalue().splitlines(),
        count_mode="--count" in cleaned,
    )

    for line in traversal:
        print(line)
    for oid in omitted:
        print(f"~{oid}")
    for line in missing:
        print(line)
    if count_line is not None:
        print(count_line)
    return code
