"""Git-compatible ``rev-list --filter-print-omitted`` presentation.

The object-filter adapter already performs metadata-only filtering without
materializing promisor objects. This adapter adds the complementary ``~<oid>``
omission channel for filters that actually collect omitted objects in native
Git while keeping unresolved foreign promises out of the repository-visible
hash domain.

Phase254 composes the omission channel with ``--count``. Git emits normal
traversal records first, then omitted records, then missing-object diagnostics,
and finally the count. Phase255 extends the same structured ordering to
``--boundary`` and includes boundary snapshots when computing blob omissions.
It also corrects an earlier compatibility assumption: Git 2.55's
``object:type`` filter does not populate the omitted-object set, so pygit must
not invent ``~`` records for objects merely hidden by that filter. Phase256
composes the same line-oriented omission channel with ``--objects-edge``:
explicit exclusion edges remain leading ``-<local SHA-256>`` traversal records,
while filtered blobs from the selected object closure are emitted later through
the independent ``~<local SHA-256>`` omission channel.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Optional, Sequence

from . import rev_list_filter_cli as _filter
from . import rev_list_promisor_cli as _promisor


_FILTER_PRINT_OMITTED = "--filter-print-omitted"
_DEFERRED_WITH_OMITTED = {"-z"}


def _omitted_local_oids(repo, argv: Sequence[str], *, spec: str) -> tuple[str, ...]:
    """Return genuine local SHA-256 ids collected by the active Git filter.

    Git 2.55's ``filter_blobs_none()`` records filtered blobs in the omission
    set, while ``filter_object_type()`` explicitly leaves its ``omits`` argument
    unused. Therefore ``object:type`` may suppress traversal output but produces
    no ``~`` records under ``--filter-print-omitted``. Matching that distinction
    is important: reporting every filtered object would look plausible but would
    diverge from native Git.

    For ``blob:none``, boundary traversal needs the same snapshot roots as the
    underlying filter adapter. Otherwise blobs that are visited only because
    ``--boundary`` exposes an older boundary snapshot would be silently absent
    from the omission set. ``--objects-edge`` needs no extra inventory roots:
    its ``-<oid>`` records advertise excluded commits as a presentation channel,
    while the normal revision grammar already keeps their object closure out of
    the selected inventory.

    The textual omitted-object channel is a repository object-id channel. If an
    unresolved promise itself would have to be reported as omitted, pygit fails
    rather than substituting its foreign/native transport identity for a local
    SHA-256 object id.
    """

    if spec.startswith("object:type="):
        return ()
    if spec != "blob:none":
        raise RuntimeError(f"unsupported omitted-object filter projection: {spec}")

    projected = _filter._project(argv)
    parsed = _filter._parse_inventory_request(projected)

    snapshot_commits = None
    if parsed["boundary"]:
        boundary_commits = _promisor._promisor_boundary_commits(
            repo,
            parsed["revisions"],
            all_refs=parsed["all_refs"],
            first_parent=parsed["first_parent"],
            topo_order=parsed["topo_order"],
            reverse=parsed["reverse"],
            skip=parsed["skip"],
            max_count=parsed["max_count"],
        )
        snapshot_commits = tuple(oid for oid, _is_boundary in boundary_commits)

    entries = _promisor.promisor_object_inventory(
        repo,
        parsed["revisions"],
        all_refs=parsed["all_refs"],
        first_parent=parsed["first_parent"],
        topo_order=parsed["topo_order"],
        reverse=parsed["reverse"],
        skip=parsed["skip"],
        max_count=parsed["max_count"],
        snapshot_commits=snapshot_commits,
    )

    omitted: list[str] = []
    for entry in entries:
        if entry.type_name != "blob":
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
    traversal (including object-edge and boundary records) but before
    missing-object diagnostics and the final ``--count`` line, so this helper
    only rearranges presentation; it does not recompute selection or count
    semantics.
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
