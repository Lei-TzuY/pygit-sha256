"""Modern status rendering with Git-style unmerged XY codes.

Phase 150 makes the persistent multi-stage index authoritative for conflict
classification. The legacy Repository.status() API remains untouched; this
module normalizes its ordinary staged/unstaged/untracked data and overlays the
seven Git porcelain conflict states derived from stages 1/2/3. Phase 151 adds
porcelain-v2 rendering without changing that normalized status model. Phase 152
adds reflog-backed stash-count reporting for long and porcelain-v2 status.
Phase 153 makes short/porcelain-v1 pathname framing machine-safe and lets ``-z``
imply porcelain v1, matching Git's command-line protocol. Phase 154 adds Git's
``-u/--untracked-files`` display modes while keeping Repository.status()'s
individual-path API unchanged. Phase 155 extends ``--ignored`` with Git's
traditional, matching, and no modes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .repo import Repository
from .status_untracked import apply_status_path_modes


_CONFLICT_CODES: Dict[Tuple[bool, bool, bool], str] = {
    (True, False, False): "DD",
    (False, True, False): "AU",
    (True, True, False): "UD",
    (False, False, True): "UA",
    (True, False, True): "DU",
    (False, True, True): "AA",
    (True, True, True): "UU",
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


def _normalized_status(
    repo: Repository,
    *,
    ignored: bool,
    untracked_mode: str = "normal",
) -> Tuple[dict, List[UnmergedStatus]]:
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
    result = apply_status_path_modes(
        repo,
        result,
        untracked_mode=untracked_mode,
        ignored=ignored,
    )
    return result, unmerged


def status_records(
    repo: Repository,
    *,
    ignored: bool = False,
    untracked_mode: str = "normal",
) -> List[StatusRecord]:
    """Return sorted porcelain-v1 records, with unmerged stages taking priority."""
    result, unmerged = _normalized_status(
        repo,
        ignored=ignored,
        untracked_mode=untracked_mode,
    )
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


def _quote_path(path: str) -> str:
    """Return Git-style C-quoted pathname text when a record needs quoting."""
    if path and all(0x20 <= ord(ch) < 0x7f and ch not in {'"', '\\'} for ch in path):
        return path
    return json.dumps(path, ensure_ascii=False)


def _short_path(path: str, *, nul: bool) -> str:
    return path if nul else _quote_path(path)


def _print_short(
    repo: Repository,
    *,
    branch: bool,
    ignored: bool,
    nul: bool = False,
    untracked_mode: str = "normal",
) -> None:
    result, _unmerged = _normalized_status(
        repo,
        ignored=ignored,
        untracked_mode=untracked_mode,
    )
    lines: List[str] = []
    if branch:
        lines.append(_branch_header(result))
    lines.extend(
        f"{record.code} {_short_path(record.path, nul=nul)}"
        for record in status_records(
            repo,
            ignored=ignored,
            untracked_mode=untracked_mode,
        )
    )
    if not lines:
        return
    separator = "\0" if nul else "\n"
    sys.stdout.write(separator.join(lines) + separator)


def _stash_summary(repo: Repository) -> Optional[str]:
    from .status_porcelain_v2 import stash_count

    count = stash_count(repo)
    if not count:
        return None
    noun = "entry" if count == 1 else "entries"
    return f"Your stash currently has {count} {noun}"


def _print_full(
    repo: Repository,
    *,
    ignored: bool,
    show_stash: bool = False,
    untracked_mode: str = "normal",
) -> None:
    result, unmerged = _normalized_status(
        repo,
        ignored=ignored,
        untracked_mode=untracked_mode,
    )

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
    clean = not unmerged and not any(result.get(key) for key in keys)
    if clean:
        print("nothing to commit, working tree clean")
    else:
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

    if show_stash:
        summary = _stash_summary(repo)
        if summary:
            if clean:
                print()
            print(summary)


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
        metavar="{v1,v2}",
        help="machine-readable porcelain v1 or v2 output",
    )
    parser.add_argument("-b", "--branch", action="store_true", help="show branch/upstream information")
    parser.add_argument(
        "--ignored",
        nargs="?",
        const="traditional",
        default=False,
        choices=("traditional", "matching", "no"),
        metavar="{traditional,matching,no}",
        help="show ignored paths using traditional (default), matching, or no mode",
    )
    parser.add_argument(
        "-u",
        "--untracked-files",
        nargs="?",
        const="all",
        default="normal",
        choices=("no", "normal", "all"),
        metavar="{no,normal,all}",
        help="show untracked files: no, normal (default), or all",
    )
    parser.add_argument(
        "-z",
        action="store_true",
        help="terminate porcelain records with NUL bytes; implies --porcelain=v1",
    )
    parser.set_defaults(show_stash=False)
    parser.add_argument(
        "--show-stash",
        dest="show_stash",
        action="store_true",
        help="show the number of entries currently stashed away",
    )
    parser.add_argument(
        "--no-show-stash",
        dest="show_stash",
        action="store_false",
        help="suppress stash-count status information",
    )
    args = parser.parse_args(list(argv))

    if args.z and args.porcelain is None:
        args.porcelain = "v1"

    repo = _find_repo()
    if args.porcelain == "v2":
        from .status_porcelain_v2 import render_porcelain_v2

        sys.stdout.write(
            render_porcelain_v2(
                repo,
                branch=args.branch,
                ignored=args.ignored,
                nul=args.z,
                show_stash=args.show_stash,
                untracked_mode=args.untracked_files,
            )
        )
    elif args.short or args.porcelain is not None:
        _print_short(
            repo,
            branch=args.branch,
            ignored=args.ignored,
            nul=args.z,
            untracked_mode=args.untracked_files,
        )
    else:
        _print_full(
            repo,
            ignored=args.ignored,
            show_stash=args.show_stash,
            untracked_mode=args.untracked_files,
        )
    return 0
