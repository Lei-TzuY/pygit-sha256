"""Modern status rendering with Git-style unmerged XY codes.

Phase 150 makes the persistent multi-stage index authoritative for conflict
classification. The legacy Repository.status() API remains untouched; this
module normalizes its ordinary staged/unstaged/untracked data and overlays the
seven Git porcelain conflict states derived from stages 1/2/3. Phase 151 adds
porcelain-v2 rendering without changing that normalized status model.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .repo import Repository


_CONFLICT_CODES: Dict[Tuple[bool, bool, bool], str] = {
    (True, False, False): "DD",   # both deleted
    (False, True, False): "AU",   # added by us
    (True, True, False): "UD",    # deleted by them
    (False, False, True): "UA",   # added by them
    (True, False, True): "DU",    # deleted by us
    (False, True, True): "AA",    # both added
    (True, True, True): "UU",     # both modified
}

_CONFLICT_LABELS = {
    "DD": "both deleted",
    "AU": "added by us",
    "UD": "deleted by them",
    "UA": "added by them",
    "DU": "deleted by us",
    "AA": "both added",
    "UU": "both modified",
}


@dataclass(frozen=True)
class UnmergedStatus:
    """One unmerged path classified from index-stage presence."""

    path: str
    code: str
    stages: Tuple[int, ...]


@dataclass(frozen=True)
class StatusRecord:
    """One porcelain-v1/short status record."""

    path: str
    code: str


def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def unmerged_status(repo: Repository) -> List[UnmergedStatus]:
    """Return Git-style unmerged classifications derived from stages 1/2/3."""
    paths = sorted({entry.path for entry in repo.index.stage_entries()})
    records: List[UnmergedStatus] = []
    for path in paths:
        present = tuple(repo.index.get(path, stage) is not None for stage in (1, 2, 3))
        code = _CONFLICT_CODES.get(present)
        if code is None:
            raise RuntimeError(f"invalid unmerged index stage combination for {path!r}: {present}")
        stages = tuple(stage for stage, exists in zip((1, 2, 3), present) if exists)
        records.append(UnmergedStatus(path=path, code=code, stages=stages))
    return records


def _kind_code(kind: str, *, index_side: bool) -> str:
    if kind == "new file":
        return "A"
    if kind == "deleted":
        return "D"
    if kind == "modified":
        return "M"
    raise RuntimeError(f"unsupported status kind: {kind!r}")


def _normalized_status(repo: Repository, *, ignored: bool) -> Tuple[dict, List[UnmergedStatus]]:
    result = repo.status(ignored=ignored)
    unmerged = unmerged_status(repo)
    conflict_paths = {record.path for record in unmerged}

    result = dict(result)
    result["staged"] = [item for item in result["staged"] if item[1] not in conflict_paths]
    result["unstaged"] = [item for item in result["unstaged"] if item[1] not in conflict_paths]
    result["untracked"] = [path for path in result["untracked"] if path not in conflict_paths]
    if "ignored" in result:
        result["ignored"] = [path for path in result["ignored"] if path not in conflict_paths]
    result["conflicts"] = sorted(conflict_paths)
    result["unmerged"] = unmerged
    return result, unmerged


def status_records(repo: Repository, *, ignored: bool = False) -> List[StatusRecord]:
    """Return sorted porcelain-v1 records, with unmerged stages taking priority."""
    result, unmerged = _normalized_status(repo, ignored=ignored)
    codes: Dict[str, List[str]] = {}

    for kind, path in result["staged"]:
        codes.setdefault(path, [" ", " "])[0] = _kind_code(kind, index_side=True)
    for kind, path in result["unstaged"]:
        codes.setdefault(path, [" ", " "])[1] = _kind_code(kind, index_side=False)

    for record in unmerged:
        codes[record.path] = [record.code[0], record.code[1]]

    for path in result["untracked"]:
        codes[path] = ["?", "?"]
    if ignored:
        for path in result.get("ignored", []):
            codes[path] = ["!", "!"]

    return [StatusRecord(path=path, code="".join(codes[path])) for path in sorted(codes)]


def _branch_header(result: dict) -> str:
    branch = result.get("branch") or "HEAD"
    upstream = result.get("upstream")
    if not isinstance(upstream, dict):
        return f"## {branch}"

    upstream_name = upstream.get("upstream")
    if not upstream_name:
        return f"## {branch}"

    details: List[str] = []
    ahead = int(upstream.get("ahead") or 0)
    behind = int(upstream.get("behind") or 0)
    if ahead:
        details.append(f"ahead {ahead}")
    if behind:
        details.append(f"behind {behind}")
    suffix = f" [{', '.join(details)}]" if details else ""
    return f"## {branch}...{upstream_name}{suffix}"


def _print_short(repo: Repository, *, branch: bool, ignored: bool, nul: bool = False) -> None:
    result, _unmerged = _normalized_status(repo, ignored=ignored)
    lines: List[str] = []
    if branch:
        lines.append(_branch_header(result))
    lines.extend(f"{record.code} {record.path}" for record in status_records(repo, ignored=ignored))
    if not lines:
        return
    separator = "\0" if nul else "\n"
    sys.stdout.write(separator.join(lines) + separator)


def _print_full(repo: Repository, *, ignored: bool) -> None:
    result, unmerged = _normalized_status(repo, ignored=ignored)

    branch = result["branch"] or "HEAD (detached)"
    print(f"On branch {branch}")
    if result.get("operation"):
        print(f"You are currently in a {result['operation']} operation.")
    print()

    keys = ("staged", "unstaged", "untracked", "ignored") if ignored else (
        "staged",
        "unstaged",
        "untracked",
    )
    if not unmerged and not any(result.get(key) for key in keys):
        print("nothing to commit, working tree clean")
        return

    if unmerged:
        print("Unmerged paths:")
        for record in unmerged:
            print(f"\t{_CONFLICT_LABELS[record.code]}:\t{record.path}")
        print()

    if result["staged"]:
        print("Changes to be committed:")
        for kind, path in result["staged"]:
            print(f"\t{kind}:\t{path}")
        print()

    if result["unstaged"]:
        print("Changes not staged for commit:")
        for kind, path in result["unstaged"]:
            print(f"\t{kind}:\t{path}")
        print()

    if result["untracked"]:
        print("Untracked files:")
        for path in result["untracked"]:
            print(f"\t{path}")
        print()

    if ignored and result.get("ignored"):
        print("Ignored files:")
        for path in result["ignored"]:
            print(f"\t{path}")
        print()


def run_status(argv: Sequence[str]) -> int:
    """Run modern status output while preserving the legacy visible surface."""
    parser = argparse.ArgumentParser(
        prog="pygit status",
        description="Show working-tree status with multi-stage conflict classification.",
    )
    parser.add_argument("-s", "--short", action="store_true", help="show short-format status")
    parser.add_argument(
        "--porcelain",
        nargs="?",
        const="v1",
        choices=("v1", "v2"),
        metavar="VERSION",
        help="machine-readable porcelain v1 or v2 output",
    )
    parser.add_argument("-b", "--branch", action="store_true", help="show branch/upstream information")
    parser.add_argument("--ignored", action="store_true", help="show ignored files")
    parser.add_argument("-z", action="store_true", help="terminate porcelain records with NUL bytes")
    args = parser.parse_args(list(argv))

    if args.z and args.porcelain is None:
        parser.error("-z requires --porcelain")

    repo = _find_repo()
    if args.porcelain == "v2":
        from .status_porcelain_v2 import render_porcelain_v2

        sys.stdout.write(
            render_porcelain_v2(
                repo,
                branch=args.branch,
                ignored=args.ignored,
                nul=args.z,
            )
        )
    elif args.short or args.porcelain is not None:
        _print_short(repo, branch=args.branch, ignored=args.ignored, nul=args.z)
    else:
        _print_full(repo, ignored=args.ignored)
    return 0
