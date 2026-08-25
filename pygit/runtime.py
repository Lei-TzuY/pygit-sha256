"""Installed CLI router for plumbing commands with shared object resolution."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .cat_file import inspect_object, object_exists
from .checkout_index import checkout_index
from .command import main as command_main
from .entrypoint import _find_repo
from .objects import CommitObject, TreeObject
from .plumbing import list_refs
from .rev_list_cli import run_rev_list
from .revision import (
    abbreviate_oid,
    glob_refs,
    namespace_refs,
    resolve_revision,
    short_refname,
    symbolic_refname,
)


def _run_cat_file(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit cat-file",
        description="Inspect SHA-256 objects and tree paths.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-t", "--type", action="store_true", help="show object type")
    mode.add_argument("-s", "--size", action="store_true", help="show object size")
    mode.add_argument("-p", "--pretty", action="store_true", help="pretty-print object content")
    mode.add_argument("-e", "--exists", action="store_true", help="test whether OBJECT exists")
    mode.add_argument("--batch", action="store_true", help="read object names from stdin and emit header plus raw content")
    mode.add_argument("--batch-check", action="store_true", help="read object names from stdin and emit metadata only")
    parser.add_argument("object", nargs="?", metavar="OBJECT")
    args = parser.parse_args(list(argv))
    repo = _find_repo()

    if args.batch or args.batch_check:
        if args.object is not None:
            parser.error("batch modes read object names from stdin")
        output = sys.stdout.buffer
        for raw in sys.stdin:
            expression = raw.rstrip("\r\n")
            if not expression:
                output.write(b" missing\n")
                continue
            try:
                record = inspect_object(repo, expression)
            except (KeyError, ValueError, RuntimeError):
                output.write(expression.encode("utf-8") + b" missing\n")
                continue

            header = f"{record.oid} {record.type_name} {record.size}\n".encode("ascii")
            output.write(header)
            if args.batch:
                output.write(record.content)
                output.write(b"\n")
        return 0

    if not args.object:
        parser.error("single-object modes require OBJECT")
    if args.exists:
        return 0 if object_exists(repo, args.object) else 1

    record = inspect_object(repo, args.object)
    if args.type:
        print(record.type_name)
        return 0
    if args.size:
        print(record.size)
        return 0

    obj = repo.store.read(record.oid)
    if isinstance(obj, CommitObject):
        print(obj.pretty_print(record.oid))
    elif isinstance(obj, TreeObject):
        for entry in obj.entries:
            kind = "tree" if entry.is_dir else "blob"
            print(f"{entry.mode} {kind} {entry.sha}\t{entry.name}")
    else:
        sys.stdout.buffer.write(record.content)
    return 0


def _run_checkout_index(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pygit checkout-index",
        description="Copy files from the index to the working tree.",
    )
    parser.add_argument("-a", "--all", action="store_true", help="checkout all index entries")
    parser.add_argument("-f", "--force", action="store_true", help="overwrite existing files")
    parser.add_argument("--prefix", default="", metavar="PREFIX", help="write entries beneath PREFIX")
    parser.add_argument("path", nargs="*", metavar="PATH")
    args = parser.parse_args(list(argv))
    if args.all and args.path:
        parser.error("--all cannot be combined with explicit paths")

    repo = _find_repo()
    checkout_index(
        repo,
        args.path,
        all_entries=args.all,
        force=args.force,
        prefix=args.prefix,
    )
    return 0


def _single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _path_value(path: Path, path_format: str) -> str:
    resolved = path.resolve()
    if path_format == "absolute":
        return str(resolved)
    try:
        relative = resolved.relative_to(Path.cwd().resolve())
        return relative.as_posix() or "."
    except ValueError:
        return os.path.relpath(str(resolved), str(Path.cwd().resolve()))


def _metadata_value(kind: str, value: Optional[str], path_format: str) -> str:
    if kind == "resolve-git-dir":
        assert value is not None
        candidate = Path(value).resolve()
        if (candidate / ".pygit").is_dir():
            candidate = candidate / ".pygit"
        if not (candidate / "HEAD").is_file():
            raise RuntimeError(f"not a pygit directory: {value}")
        return _path_value(candidate, path_format)

    repo = _find_repo()
    cwd = Path.cwd().resolve()
    pygit_dir = repo.pygit_dir.resolve()
    worktree = repo.worktree.resolve()
    inside_git = cwd == pygit_dir or pygit_dir in cwd.parents
    inside_worktree = (cwd == worktree or worktree in cwd.parents) and not inside_git

    if kind == "git-dir":
        return _path_value(pygit_dir, path_format)
    if kind == "absolute-git-dir":
        return str(pygit_dir)
    if kind == "git-common-dir":
        return _path_value(pygit_dir, path_format)
    if kind == "show-toplevel":
        return _path_value(worktree, path_format)
    if kind == "show-prefix":
        if cwd == worktree or worktree not in cwd.parents:
            return ""
        return cwd.relative_to(worktree).as_posix().rstrip("/") + "/"
    if kind == "show-cdup":
        if cwd == worktree or worktree not in cwd.parents:
            return ""
        return "../" * len(cwd.relative_to(worktree).parts)
    if kind == "is-inside-work-tree":
        return "true" if inside_worktree else "false"
    if kind == "is-inside-git-dir":
        return "true" if inside_git else "false"
    if kind == "is-bare-repository":
        return "false"
    if kind == "is-shallow-repository":
        return "true" if (pygit_dir / "shallow").exists() else "false"
    if kind == "show-object-format":
        return "sha256"
    if kind == "show-ref-format":
        return "files"
    if kind == "git-path":
        assert value is not None
        return _path_value(pygit_dir / value, path_format)
    raise ValueError(f"unknown rev-parse metadata query: {kind}")


def _parse_option_value(argv: Sequence[str], index: int, option: str) -> Tuple[str, int]:
    arg = argv[index]
    prefix = option + "="
    if arg.startswith(prefix):
        return arg[len(prefix) :], index
    if index + 1 >= len(argv):
        raise ValueError(f"{option} requires a value")
    return argv[index + 1], index + 1


def _run_rev_parse(argv: Sequence[str]) -> int:
    """Focused but script-useful ``git rev-parse`` compatible plumbing."""
    verify = False
    quiet = False
    short: Optional[int] = None
    symbolic_full = False
    abbrev_ref = False
    sq = False
    revs_only = False
    no_revs = False
    default: Optional[str] = None
    path_format = "absolute"
    negate = False
    end_options = False

    positionals: List[Tuple[str, bool]] = []
    namespace_specs: List[Tuple[str, Optional[str], bool]] = []
    glob_specs: List[Tuple[str, bool]] = []
    metadata: List[Tuple[str, Optional[str]]] = []
    disambiguate: List[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if end_options:
            positionals.append((arg, negate))
            i += 1
            continue
        if arg in {"--", "--end-of-options"}:
            end_options = True
        elif arg == "--not":
            negate = not negate
        elif arg == "--verify":
            verify = True
        elif arg in {"-q", "--quiet"}:
            quiet = True
        elif arg == "--short":
            short = 12
        elif arg.startswith("--short="):
            value = arg.split("=", 1)[1]
            if not value.isdigit():
                raise ValueError("--short requires an integer length")
            short = int(value)
        elif arg == "--symbolic-full-name":
            symbolic_full = True
        elif arg == "--abbrev-ref" or arg.startswith("--abbrev-ref="):
            abbrev_ref = True
        elif arg == "--sq":
            sq = True
        elif arg == "--revs-only":
            revs_only = True
        elif arg == "--no-revs":
            no_revs = True
        elif arg == "--default" or arg.startswith("--default="):
            default, i = _parse_option_value(argv, i, "--default")
        elif arg == "--branches" or arg.startswith("--branches="):
            pattern = arg.split("=", 1)[1] if "=" in arg else None
            namespace_specs.append(("branches", pattern, negate))
        elif arg == "--tags" or arg.startswith("--tags="):
            pattern = arg.split("=", 1)[1] if "=" in arg else None
            namespace_specs.append(("tags", pattern, negate))
        elif arg == "--remotes" or arg.startswith("--remotes="):
            pattern = arg.split("=", 1)[1] if "=" in arg else None
            namespace_specs.append(("remotes", pattern, negate))
        elif arg == "--all":
            namespace_specs.append(("all", None, negate))
        elif arg == "--glob" or arg.startswith("--glob="):
            pattern, i = _parse_option_value(argv, i, "--glob")
            glob_specs.append((pattern, negate))
        elif arg == "--disambiguate" or arg.startswith("--disambiguate="):
            prefix, i = _parse_option_value(argv, i, "--disambiguate")
            disambiguate.append(prefix)
        elif arg == "--path-format" or arg.startswith("--path-format="):
            path_format, i = _parse_option_value(argv, i, "--path-format")
            if path_format not in {"absolute", "relative"}:
                raise ValueError("--path-format must be 'absolute' or 'relative'")
        elif arg in {
            "--git-dir",
            "--absolute-git-dir",
            "--git-common-dir",
            "--show-toplevel",
            "--show-prefix",
            "--show-cdup",
            "--is-inside-work-tree",
            "--is-inside-git-dir",
            "--is-bare-repository",
            "--is-shallow-repository",
            "--show-object-format",
            "--show-ref-format",
        }:
            metadata.append((arg[2:], None))
        elif arg == "--resolve-git-dir" or arg.startswith("--resolve-git-dir="):
            value, i = _parse_option_value(argv, i, "--resolve-git-dir")
            metadata.append(("resolve-git-dir", value))
        elif arg == "--git-path" or arg.startswith("--git-path="):
            value, i = _parse_option_value(argv, i, "--git-path")
            metadata.append(("git-path", value))
        elif arg.startswith("-"):
            raise ValueError(f"unsupported rev-parse option: {arg}")
        else:
            positionals.append((arg, negate))
        i += 1

    if revs_only and no_revs:
        raise ValueError("--revs-only and --no-revs are mutually exclusive")
    if symbolic_full and abbrev_ref:
        raise ValueError("--symbolic-full-name and --abbrev-ref are mutually exclusive")
    if short is not None and not 4 <= short <= 64:
        raise ValueError("--short length must be between 4 and 64")

    for kind, value in metadata:
        print(_metadata_value(kind, value, path_format))

    repo = None
    needs_repo = bool(positionals or namespace_specs or glob_specs or disambiguate or default)
    if needs_repo:
        repo = _find_repo()

    if repo is not None and disambiguate:
        for prefix in disambiguate:
            lowered = prefix.lower()
            if len(lowered) < 4 or any(char not in "0123456789abcdef" for char in lowered):
                raise ValueError("--disambiguate requires a 4+ hex prefix")
            for oid in repo.store.all_shas():
                if oid.startswith(lowered):
                    print(oid)

    if not positionals and not namespace_specs and not glob_specs and default:
        positionals.append((default, negate))

    resolved: List[Tuple[str, str, Optional[str], bool]] = []
    non_revisions: List[str] = []

    if repo is not None:
        for expression, is_negated in positionals:
            try:
                oid = resolve_revision(repo, expression)
            except (KeyError, ValueError, RuntimeError):
                if no_revs:
                    non_revisions.append(expression)
                    continue
                if revs_only:
                    continue
                if quiet:
                    return 1
                raise RuntimeError(f"bad revision {expression!r}") from None
            if no_revs:
                continue
            resolved.append((expression, oid, symbolic_refname(repo, expression), is_negated))

        if not no_revs:
            for namespace, pattern, is_negated in namespace_specs:
                records = list_refs(repo) if namespace == "all" else namespace_refs(repo, namespace, pattern)
                for oid, refname in records:
                    resolved.append((refname, oid, refname, is_negated))
            for pattern, is_negated in glob_specs:
                for oid, refname in glob_refs(repo, pattern):
                    resolved.append((refname, oid, refname, is_negated))

    if verify:
        if len(resolved) != 1 or non_revisions:
            if quiet:
                return 1
            raise RuntimeError("Needed a single revision")

    if not metadata and not disambiguate and not resolved and not non_revisions:
        if quiet:
            return 1
        raise RuntimeError("rev-parse requires a revision or option flag")

    assert repo is not None or not resolved
    for expression, oid, refname, is_negated in resolved:
        if symbolic_full:
            value = refname or expression
        elif abbrev_ref:
            value = short_refname(refname) if refname else expression
        elif short is not None:
            assert repo is not None
            value = abbreviate_oid(repo, oid, short)
        else:
            value = oid
        if is_negated:
            value = "^" + value
        print(_single_quote(value) if sq else value)

    for value in non_revisions:
        print(_single_quote(value) if sq else value)
    return 0


def main() -> None:
    argv = sys.argv[1:]
    try:
        if argv and argv[0] == "cat-file":
            code = _run_cat_file(argv[1:])
        elif argv and argv[0] == "checkout-index":
            code = _run_checkout_index(argv[1:])
        elif argv and argv[0] == "rev-parse":
            code = _run_rev_parse(argv[1:])
        elif argv and argv[0] == "rev-list":
            code = run_rev_list(argv[1:])
        else:
            command_main()
            return
    except (RuntimeError, ValueError, KeyError, FileNotFoundError, FileExistsError, IsADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    if code:
        raise SystemExit(code)
