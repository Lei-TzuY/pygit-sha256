"""
pygit/cli.py
============
Command-line interface for pygit.

Supported sub-commands
----------------------
  init        – initialise a repository
  hash-object – hash (and optionally store) a file as a blob
  cat-file    – show an object's type, size, or content
  add         – stage files
  rm          – unstage / remove files
  commit      – create a commit
  log         – show commit history
  status      – show working-tree status
  diff        – show changes as a unified diff
  branch      – list, create, or delete branches
  checkout    – switch branch or restore working tree
  tag         – list or create lightweight tags
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .objects import BlobObject, CommitObject, TreeObject
from .repo import Repository


# ------------------------------------------------------------------
# Helper: locate repo by walking up from CWD
# ------------------------------------------------------------------

def _find_repo() -> Repository:
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / ".pygit").is_dir():
            return Repository(str(candidate))
    raise RuntimeError(
        "fatal: not a pygit repository "
        "(or any of the parent directories): .pygit"
    )


def _parse_author(s: str):
    """Parse 'Name <email>' into (name, email), returning defaults on failure."""
    m = re.match(r"^(.*?)\s*<(.+?)>\s*$", s.strip())
    if m:
        return m.group(1).strip(), m.group(2)
    return s, "unknown@example.com"


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    Repository.init(args.directory)


def cmd_clone(args: argparse.Namespace) -> None:
    repo = Repository.clone(args.url, args.directory)
    print(f"Cloned {args.url} into {repo.worktree}")


def cmd_hash_object(args: argparse.Namespace) -> None:
    data = Path(args.file).read_bytes()
    if args.write:
        repo = _find_repo()
        print(repo.hash_object(data, write=True))
    else:
        print(BlobObject(data).hash())


def cmd_cat_file(args: argparse.Namespace) -> None:
    repo = _find_repo()
    obj  = repo.cat_file(args.sha)

    if args.type:
        print(obj.type_name.decode())
        return
    if args.size:
        print(len(obj.serialize()))
        return
    # -p  pretty-print
    if isinstance(obj, CommitObject):
        print(obj.pretty_print(args.sha))
    elif isinstance(obj, TreeObject):
        for entry in obj.entries:
            kind = "tree" if entry.is_dir else "blob"
            print(f"{entry.mode} {kind} {entry.sha}\t{entry.name}")
    else:
        sys.stdout.buffer.write(obj.serialize())


def cmd_add(args: argparse.Namespace) -> None:
    _find_repo().add(args.pathspec)


def cmd_rm(args: argparse.Namespace) -> None:
    repo = _find_repo()
    for path in args.pathspec:
        repo.rm(path, cached=args.cached)


def cmd_commit(args: argparse.Namespace) -> None:
    repo  = _find_repo()
    name, email = _parse_author(args.author) if args.author else ("Unknown", "unknown@example.com")
    sha   = repo.commit(args.message, author_name=name, author_email=email)
    label = repo.refs.current_branch() or "HEAD"
    print(f"[{label} {sha[:12]}] {args.message.splitlines()[0]}")


def cmd_log(args: argparse.Namespace) -> None:
    repo    = _find_repo()
    commits = repo.log(max_count=args.n or 0)
    if not commits:
        print("(no commits yet)")
        return
    for sha, commit in commits:
        if args.oneline:
            print(f"{sha[:12]} {commit.message.splitlines()[0]}")
        else:
            print(commit.pretty_print(sha))
            print()


def cmd_status(args: argparse.Namespace) -> None:
    repo   = _find_repo()
    result = repo.status()

    branch = result["branch"] or "HEAD (detached)"
    print(f"On branch {branch}\n")

    if not any(result[k] for k in ("staged", "unstaged", "untracked", "conflicts")):
        print("nothing to commit, working tree clean")
        return

    if result["conflicts"]:
        print("Unmerged paths:")
        for path in result["conflicts"]:
            print(f"\tboth modified:\t{path}")
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


def cmd_diff(args: argparse.Namespace) -> None:
    output = _find_repo().diff(cached=args.cached)
    if output:
        sys.stdout.write(output)


def cmd_branch(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.delete:
        if not args.name:
            print("error: branch name required with -d", file=sys.stderr)
            sys.exit(1)
        repo.branch(args.name, delete=True)
        print(f"Deleted branch '{args.name}'.")
    elif args.name:
        repo.branch(args.name)
        print(f"Switched to a new branch '{args.name}'.")
    else:
        branches = repo.branch() or []
        current  = repo.refs.current_branch()
        for b in branches:
            marker = "* " if b == current else "  "
            print(f"{marker}{b}")


def cmd_checkout(args: argparse.Namespace) -> None:
    repo = _find_repo()
    repo.checkout(args.target)
    if repo.refs.get_branch(args.target):
        print(f"Switched to branch '{args.target}'")
    else:
        sha = repo.refs.resolve_head() or args.target
        print(f"HEAD is now at {sha[:12]}")


def cmd_tag(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.name:
        repo.tag(args.name, args.commit)
        print(f"Created tag '{args.name}'.")
    else:
        for t in repo.tag() or []:
            print(t)


def cmd_merge(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.abort:
        result = repo.merge_abort()
        print(f"Merge aborted; restored {str(result['sha'])[:12]}")
        return
    if not args.target:
        raise RuntimeError("merge requires a target")
    result = repo.merge(args.target, message=args.message)
    status = result["status"]
    if status == "up-to-date":
        print("Already up to date.")
    elif status == "fast-forward":
        print(f"Fast-forward to {str(result['sha'])[:12]}")
    elif status == "merged":
        print(f"Merge made commit {str(result['sha'])[:12]}")
    else:
        print("Automatic merge failed; fix conflicts and commit the result.")
        for path in result["conflicts"]:
            print(f"CONFLICT (content): Merge conflict in {path}")


def _print_replay_result(operation: str, result: dict) -> None:
    status = result["status"]
    sha = result.get("sha")
    if status == "conflicts":
        print(f"{operation} stopped due to conflicts.")
        for path in result["conflicts"]:
            print(f"CONFLICT (content): Merge conflict in {path}")
    elif sha:
        print(f"{operation}: {status} {str(sha)[:12]}")
    else:
        print(f"{operation}: {status}")


def cmd_cherry_pick(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.continue_:
        result = repo.cherry_pick_continue()
    elif args.abort:
        result = repo.cherry_pick_abort()
    else:
        if not args.target:
            raise RuntimeError("cherry-pick requires a commit")
        result = repo.cherry_pick(args.target)
    _print_replay_result("cherry-pick", result)


def cmd_rebase(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.continue_:
        result = repo.rebase_continue()
    elif args.skip:
        result = repo.rebase_skip()
    elif args.abort:
        result = repo.rebase_abort()
    else:
        if not args.target:
            raise RuntimeError("rebase requires a target")
        result = repo.rebase(args.target)
    _print_replay_result("rebase", result)


def cmd_bisect(args: argparse.Namespace) -> None:
    repo = _find_repo()
    revisions = args.revisions
    if args.action == "start":
        if len(revisions) > 2:
            raise RuntimeError("bisect start accepts at most BAD and GOOD revisions")
        result = repo.bisect_start(*revisions)
    elif args.action == "good":
        if len(revisions) > 1:
            raise RuntimeError("bisect good accepts at most one revision")
        result = repo.bisect_good(revisions[0] if revisions else None)
    elif args.action == "bad":
        if len(revisions) > 1:
            raise RuntimeError("bisect bad accepts at most one revision")
        result = repo.bisect_bad(revisions[0] if revisions else None)
    else:
        if revisions:
            raise RuntimeError("bisect reset does not accept a revision")
        result = repo.bisect_reset()

    status = result["status"]
    sha = result.get("sha")
    if status == "found":
        print(f"{str(sha)[:12]} is the first bad commit")
    elif status == "testing":
        print(f"Bisecting: test {str(sha)[:12]} ({result['remaining']} candidates)")
    elif status == "awaiting":
        print("Bisect started; mark both a good and a bad revision.")
    else:
        print(f"Bisect {status}: {str(sha)[:12]}")


def cmd_stash(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.action == "push":
        sha = repo.stash_push(args.message or "WIP on current branch")
        print(f"Saved working directory and index state {sha[:12]}")
    elif args.action == "pop":
        sha = repo.stash_pop()
        print(f"Dropped refs/stash@{{0}} ({sha[:12]})")
    else:
        for i, (sha, commit) in enumerate(repo.stash_list()):
            print(f"stash@{{{i}}}: {sha[:12]} {commit.message}")


def cmd_reflog(args: argparse.Namespace) -> None:
    for i, entry in enumerate(_find_repo().reflog(args.ref)):
        print(f"{entry.new_sha[:12]} {args.ref}@{{{i}}}: {entry.message}")


def cmd_remote(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.action == "add":
        if not args.name or not args.url:
            raise RuntimeError("remote add requires NAME and URL")
        repo.add_remote(args.name, args.url)
        return
    if args.action == "remove":
        if not args.name:
            raise RuntimeError("remote remove requires NAME")
        repo.remove_remote(args.name)
        return
    if args.action == "rename":
        if not args.name or not args.url:
            raise RuntimeError("remote rename requires OLD and NEW")
        repo.rename_remote(args.name, args.url)
        return
    if args.action == "prune":
        if not args.name:
            raise RuntimeError("remote prune requires NAME")
        result = repo.prune_remote(args.name)
        for branch in result["pruned"]:
            print(f"Pruned {args.name}/{branch}")
        return
    for name, url in sorted(repo.list_remotes().items()):
        print(f"{name}\t{url}" if args.verbose else name)


def cmd_fetch(args: argparse.Namespace) -> None:
    result = _find_repo().fetch(args.remote)
    print(f"Fetched {len(result['refs'])} refs from {args.remote}")


def cmd_pull(args: argparse.Namespace) -> None:
    result = _find_repo().pull(args.remote)
    print(f"Pull result: {result['status']}")


def cmd_push(args: argparse.Namespace) -> None:
    result = _find_repo().push(args.remote, force=args.force)
    print(
        f"Push result: {result['status']} "
        f"{result['remote']}/{result['branch']} ({result['objects']} objects)"
    )


def cmd_reset(args: argparse.Namespace) -> None:
    mode = "hard" if args.hard else "soft" if args.soft else "mixed"
    repo = _find_repo()
    if args.pathspec:
        if mode != "mixed":
            raise RuntimeError("pathspec reset only supports mixed mode")
        result = repo.reset_paths(args.pathspec, target=args.target)
        print(f"Reset {len(result['paths'])} path(s) to {str(result['sha'])[:12]}")
        return
    result = repo.reset(args.target, mode=mode)
    print(f"Reset {mode} to {str(result['sha'])[:12]}")


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pygit",
        description="A minimal Git clone in Python.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # init
    p = sub.add_parser("init", help="Initialise a new repository")
    p.add_argument("directory", nargs="?", default=".", metavar="DIR")
    p.set_defaults(func=cmd_init)

    # clone
    p = sub.add_parser("clone", help="Clone a smart HTTP Git repository")
    p.add_argument("url", metavar="URL")
    p.add_argument("directory", nargs="?", metavar="DIR")
    p.set_defaults(func=cmd_clone)

    # hash-object
    p = sub.add_parser("hash-object", help="Compute SHA for a file")
    p.add_argument("-w", dest="write", action="store_true",
                   help="Write to object store")
    p.add_argument("file", metavar="FILE")
    p.set_defaults(func=cmd_hash_object)

    # cat-file
    p = sub.add_parser("cat-file", help="Show object content/type/size")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-t", dest="type",   action="store_true", help="Show type")
    g.add_argument("-s", dest="size",   action="store_true", help="Show size in bytes")
    g.add_argument("-p", dest="pretty", action="store_true", help="Pretty-print content")
    p.add_argument("sha", metavar="SHA")
    p.set_defaults(func=cmd_cat_file)

    # add
    p = sub.add_parser("add", help="Stage files")
    p.add_argument("pathspec", nargs="+", metavar="PATH")
    p.set_defaults(func=cmd_add)

    # rm
    p = sub.add_parser("rm", help="Remove files from index (and optionally disk)")
    p.add_argument("--cached", action="store_true",
                   help="Remove from index only, leave file on disk")
    p.add_argument("pathspec", nargs="+", metavar="PATH")
    p.set_defaults(func=cmd_rm)

    # commit
    p = sub.add_parser("commit", help="Record changes to the repository")
    p.add_argument("-m", dest="message", required=True, metavar="MSG",
                   help="Commit message")
    p.add_argument("--author", metavar="'Name <email>'",
                   help="Override the commit author")
    p.set_defaults(func=cmd_commit)

    # log
    p = sub.add_parser("log", help="Show commit history")
    p.add_argument("--oneline", action="store_true",
                   help="Condense each commit to a single line")
    p.add_argument("-n", type=int, default=0, metavar="N",
                   help="Limit to N most recent commits")
    p.set_defaults(func=cmd_log)

    # status
    p = sub.add_parser("status", help="Show the working-tree status")
    p.set_defaults(func=cmd_status)

    # diff
    p = sub.add_parser("diff", help="Show changes as a unified diff")
    p.add_argument("--cached", action="store_true",
                   help="Compare index vs HEAD instead of working tree vs index")
    p.set_defaults(func=cmd_diff)

    # branch
    p = sub.add_parser("branch", help="List, create, or delete branches")
    p.add_argument("-d", dest="delete", action="store_true",
                   help="Delete the named branch")
    p.add_argument("name", nargs="?", metavar="NAME")
    p.set_defaults(func=cmd_branch)

    # checkout
    p = sub.add_parser("checkout", help="Switch branches or restore the working tree")
    p.add_argument("target", metavar="BRANCH|SHA")
    p.set_defaults(func=cmd_checkout)

    # tag
    p = sub.add_parser("tag", help="List or create lightweight tags")
    p.add_argument("name",   nargs="?", metavar="NAME")
    p.add_argument("commit", nargs="?", metavar="COMMIT",
                   help="Commit to tag (default: HEAD)")
    p.set_defaults(func=cmd_tag)

    # merge
    p = sub.add_parser("merge", help="Join another development history")
    merge_action = p.add_mutually_exclusive_group()
    merge_action.add_argument("--abort", action="store_true",
                              help="Abort an in-progress conflicted merge")
    p.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Override the merge commit message")
    p.set_defaults(func=cmd_merge)

    # cherry-pick
    p = sub.add_parser("cherry-pick", help="Replay one commit on top of HEAD")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--continue", dest="continue_", action="store_true",
                        help="Continue after resolving conflicts")
    action.add_argument("--abort", action="store_true",
                        help="Abort and restore the original worktree")
    p.add_argument("target", nargs="?", metavar="COMMIT")
    p.set_defaults(func=cmd_cherry_pick)

    # rebase
    p = sub.add_parser("rebase", help="Replay the current branch onto another commit")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--continue", dest="continue_", action="store_true",
                        help="Continue after resolving conflicts")
    action.add_argument("--skip", action="store_true",
                        help="Skip the currently stopped commit")
    action.add_argument("--abort", action="store_true",
                        help="Abort and restore the original branch")
    p.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    p.set_defaults(func=cmd_rebase)

    # bisect
    p = sub.add_parser("bisect", help="Find the first bad commit by binary search")
    p.add_argument("action", choices=("start", "good", "bad", "reset"))
    p.add_argument("revisions", nargs="*", metavar="REV")
    p.set_defaults(func=cmd_bisect)

    # stash
    p = sub.add_parser("stash", help="Stash dirty working-tree changes")
    p.add_argument("action", nargs="?", choices=("push", "pop", "list"),
                   default="push")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Description for stash push")
    p.set_defaults(func=cmd_stash)

    # reflog
    p = sub.add_parser("reflog", help="Show recorded ref movements")
    p.add_argument("ref", nargs="?", default="HEAD", metavar="REF")
    p.set_defaults(func=cmd_reflog)

    # remote
    p = sub.add_parser("remote", help="List or add remotes")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show remote URLs")
    p.add_argument("action", nargs="?",
                   choices=("add", "remove", "rename", "prune"),
                   metavar="add|remove|rename|prune")
    p.add_argument("name", nargs="?", metavar="NAME")
    p.add_argument("url", nargs="?", metavar="URL|NEW")
    p.set_defaults(func=cmd_remote)

    # fetch / pull / push
    p = sub.add_parser("fetch", help="Fetch refs from a smart HTTP remote")
    p.add_argument("remote", nargs="?", default="origin", metavar="REMOTE")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("pull", help="Fetch and merge the current remote branch")
    p.add_argument("remote", nargs="?", default="origin", metavar="REMOTE")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("push", help="Push the current branch to a smart HTTP remote")
    p.add_argument("-f", "--force", action="store_true",
                   help="Allow a non-fast-forward remote update")
    p.add_argument("remote", nargs="?", default="origin", metavar="REMOTE")
    p.set_defaults(func=cmd_push)

    # reset
    p = sub.add_parser("reset", help="Move HEAD and optionally reset index/worktree")
    reset_mode = p.add_mutually_exclusive_group()
    reset_mode.add_argument("--soft", action="store_true",
                            help="Move HEAD only")
    reset_mode.add_argument("--mixed", action="store_true",
                            help="Move HEAD and reset the index (default)")
    reset_mode.add_argument("--hard", action="store_true",
                            help="Move HEAD, reset the index, and restore tracked files")
    p.add_argument("target", nargs="?", default="HEAD", metavar="REV")
    p.add_argument("pathspec", nargs="*", metavar="PATH")
    p.set_defaults(func=cmd_reset)

    return parser


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()
    try:
        args.func(args)
    except (RuntimeError, ValueError, KeyError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
