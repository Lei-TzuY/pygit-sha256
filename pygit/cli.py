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
  log         – show commit history (--all, --author, --grep, --graph)
  status      – show working-tree status
  diff        – show changes as a unified diff (--stat, ref args)
  show        – show a commit and its diff
  ls-files    – list tracked files
  blame       – show per-line authorship
  branch      – list, create, delete, or rename (-m) branches
  checkout    – switch branch (-b to create) or restore working tree
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


def cmd_graph(args: argparse.Namespace) -> None:
    repo = _find_repo()
    lines = repo.render_graph(max_count=args.max_count)
    for line in lines:
        print(line)


def cmd_log(args: argparse.Namespace) -> None:
    repo    = _find_repo()
    commits = repo.log(
        max_count=args.n or 0,
        all_branches=args.all,
        author=args.author or None,
        grep=args.grep or None,
    )
    if not commits:
        print("(no commits yet)")
        return
    if args.graph:
        print(repo.format_log_graph(commits))
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
    print(f"On branch {branch}")
    if result.get("operation"):
        print(f"You are currently in a {result['operation']} operation.")
    print()

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
    from_ref = getattr(args, "from_ref", None) or None
    to_ref   = getattr(args, "to_ref",   None) or None
    output = _find_repo().diff(
        cached=args.cached,
        stat=args.stat,
        from_ref=from_ref,
        to_ref=to_ref,
    )
    if output:
        sys.stdout.write(output)


def cmd_show(args: argparse.Namespace) -> None:
    output = _find_repo().show(target=args.target, stat=args.stat)
    sys.stdout.write(output)


def cmd_ls_files(args: argparse.Namespace) -> None:
    for line in _find_repo().ls_files(stage=args.stage):
        print(line)


def cmd_blame(args: argparse.Namespace) -> None:
    lines = _find_repo().blame(args.file)
    for line in lines:
        print(line)


def cmd_branch(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.delete:
        if not args.name:
            print("error: branch name required with -d", file=sys.stderr)
            sys.exit(1)
        repo.branch(args.name, delete=True)
        print(f"Deleted branch '{args.name}'.")
    elif args.move:
        if not args.name or not args.move:
            print("error: branch -m requires OLD NEW", file=sys.stderr)
            sys.exit(1)
        repo.branch(args.name, rename=args.move)
        print(f"Renamed branch '{args.name}' to '{args.move}'.")
    elif args.name:
        repo.branch(args.name)
        print(f"Created branch '{args.name}'.")
    else:
        branches = repo.branch() or []
        current  = repo.refs.current_branch()
        for b in branches:
            marker = "* " if b == current else "  "
            print(f"{marker}{b}")


def cmd_checkout(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.paths:
        target = args.target if args.target and not repo.refs.resolve(args.target) and not repo.store.resolve_prefix(args.target) else "HEAD"
        if target != "HEAD":
            restored = repo.checkout_paths(args.paths, target=target)
        else:
            # If target was actually part of paths list
            all_paths = ([args.target] if args.target else []) + args.paths
            restored = repo.checkout_paths(all_paths, target="HEAD")
        for p in restored:
            print(f"Updated {p}")
        return

    if args.create:
        # checkout -b <branch>: create branch then switch
        repo.branch(args.target)
        print(f"Switched to a new branch '{args.target}'")
        return
    repo.checkout(args.target)
    if repo.refs.get_branch(args.target):
        print(f"Switched to branch '{args.target}'")
    else:
        sha = repo.refs.resolve_head() or args.target
        print(f"HEAD is now at {sha[:12]}")


def cmd_revert(args: argparse.Namespace) -> None:
    repo = _find_repo()
    result = repo.revert(args.target)
    if result["status"] == "reverted":
        sha = str(result["sha"])
        c_obj = repo.store.read(sha)
        msg_first = c_obj.message.splitlines()[0] if isinstance(c_obj, CommitObject) else ""
        print(f"[{sha[:12]}] {msg_first}")
    else:
        print("Automatic revert failed; fix conflicts and commit.")


def cmd_shortlog(args: argparse.Namespace) -> None:
    repo = _find_repo()
    grouped = repo.shortlog(start=args.target)
    for author, titles in grouped.items():
        if args.summary:
            print(f"{author}: {len(titles)}")
        else:
            print(f"{author} ({len(titles)}):")
            for t in titles:
                print(f"      {t}")
            print()


def cmd_describe(args: argparse.Namespace) -> None:
    repo = _find_repo()
    target = args.target or "HEAD"
    desc = repo.describe(target)
    print(desc)


def cmd_repack(args: argparse.Namespace) -> None:
    repo = _find_repo()
    pack_p, idx_p = repo.repack(delete_loose=not args.keep_loose)
    print(f"Packfile created: {pack_p.name}")
    print(f"Index created:    {idx_p.name}")


def cmd_submodule(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.subcommand == "add":
        added = repo.submodule_add(args.url, args.path)
        print(f"Submodule '{added}' added and staged.")
    elif args.subcommand == "status" or not args.subcommand:
        subs = repo.submodule_list()
        for name, path, url in subs:
            print(f" {path} ({url})")


def cmd_lfs(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.subcommand == "track":
        repo.lfs_track(args.pattern)
        print(f"Tracking '{args.pattern}' with Git LFS.")
    elif args.subcommand == "ls-files" or not args.subcommand:
        from .lfs import LFSEngine
        lfs = LFSEngine(repo.pygit_dir, repo.worktree)
        for pat in lfs.list_tracked_patterns():
            print(f" {pat}")


def cmd_bundle(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.subcommand == "create":
        out_path = repo.bundle_create(args.file, target_ref=args.target or "HEAD")
        print(f"Bundle created: {out_path}")
    elif args.subcommand == "verify":
        res = repo.bundle_verify(args.file)
        print(f"{args.file} is a valid bundle.")
        for ref, sha in res["refs"].items():
            print(f"  {sha[:12]} {ref}")


def cmd_worktree(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.subcommand == "add":
        out = repo.worktree_add(args.path, branch=args.branch or "main")
        print(f"Preparing worktree (checking out '{args.branch or 'main'}')")
        print(f"HEAD is now at {str(out)}")
    elif args.subcommand == "list" or not args.subcommand:
        wts = repo.worktree_list()
        for name, path, head_ref in wts:
            print(f"{path:<40} {head_ref}")
    elif args.subcommand == "remove":
        repo.worktree_remove(args.path)
        print(f"Removed worktree '{args.path}'")


def cmd_verify_pack(args: argparse.Namespace) -> None:
    repo = _find_repo()
    entries = repo.verify_pack(args.file, verbose=args.verbose)
    for sha, t_name, size, compressed_size, offset in entries:
        if args.verbose:
            print(f"{sha[:12]} {t_name:<6} {size:<8} {compressed_size:<8} {offset}")
    print(f"{args.file}: ok")


def cmd_count_objects(args: argparse.Namespace) -> None:
    repo = _find_repo()
    info = repo.count_objects()
    if args.verbose:
        print(f"count: {info['count']}")
        print(f"size: {info['size_kb']}")
        print(f"in-pack: {info['in_pack']}")
        print(f"packs: {info['packs']}")
        print(f"size-pack: {info['size_pack_kb']}")
    else:
        print(f"{info['count']} objects, {info['size_kb']} kilobytes")


def cmd_verify_commit(args: argparse.Namespace) -> None:
    repo = _find_repo()
    res = repo.verify_commit(args.target or "HEAD")
    sha = str(res["sha"])
    if res["has_signature"]:
        print(f"gpg: Signature made for commit {sha[:12]}")
        print(f"gpg: Good signature from OpenPGP key")
        print(res["signature"])
    else:
        print(f"pygit: no signature found in commit {sha[:12]}")


def cmd_commit(args: argparse.Namespace) -> None:
    repo = _find_repo()
    msg = args.message or ""
    sha = repo.commit(message=msg, author=args.author, amend=args.amend, template=args.template)
    label = repo.refs.current_branch() or "HEAD"
    c_obj = repo.store.read(sha)
    msg_first = c_obj.message.splitlines()[0] if isinstance(c_obj, CommitObject) else ""
    print(f"[{label} {sha[:12]}] {msg_first}")


def cmd_difftool(args: argparse.Namespace) -> None:
    repo = _find_repo()
    lines = repo.difftool(from_ref=args.from_ref, to_ref=args.to_ref)
    for line in lines:
        print(line)


def cmd_mergetool(args: argparse.Namespace) -> None:
    repo = _find_repo()
    statuses = repo.mergetool()
    if not statuses:
        print("No files need merging")
        return
    for path, status in statuses:
        print(f"Normalizing merge state for '{path}': {status}")


def cmd_tag(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.name:
        repo.tag(
            args.name,
            args.commit,
            annotated=args.annotated or bool(args.message),
            message=args.message or "",
        )
        kind = "annotated" if (args.annotated or args.message) else "lightweight"
        print(f"Created {kind} tag '{args.name}'.")
    else:
        for t in repo.tag() or []:
            print(t)


def cmd_fsck(args: argparse.Namespace) -> None:
    res = _find_repo().fsck()
    for sha in res["corrupt"]:
        print(f"error in object {sha}: corrupt object")
    for sha in res["dangling"]:
        print(f"dangling object {sha}")
    print(f"Checked {res['reachable_count']} reachable objects.")


def cmd_gc(args: argparse.Namespace) -> None:
    res = _find_repo().gc(prune=not args.no_prune)
    print(f"Garbage collection: removed {res['deleted']} dangling objects, retained {res['retained']}.")


def cmd_archive(args: argparse.Namespace) -> None:
    out = _find_repo().archive(
        output_path=args.output,
        target=args.target or "HEAD",
        format=args.format or "zip",
    )
    print(f"Exported archive to {out}")


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
    elif args.action == "show":
        output = repo.stash_show(target=args.target, stat=args.stat)
        sys.stdout.write(output)
    else:
        for i, (sha, commit) in enumerate(repo.stash_list()):
            print(f"stash@{{{i}}}: {sha[:12]} {commit.message}")


def cmd_clean(args: argparse.Namespace) -> None:
    removed = _find_repo().clean(force=args.force, directories=args.directories)
    for path in removed:
        print(f"Removing {path}")


def cmd_rev_parse(args: argparse.Namespace) -> None:
    sha = _find_repo().rev_parse(args.rev)
    print(sha)


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
    p.add_argument("-m", dest="message", default="", metavar="MSG",
                   help="Commit message")
    p.add_argument("-t", "--template", metavar="TEMPLATE",
                   help="Use contents of template file as commit message")
    p.add_argument("--author", metavar="AUTHOR",
                   help="Override author ('Name <email>')")
    p.add_argument("--amend", action="store_true",
                   help="Amend previous commit")
    p.set_defaults(func=cmd_commit)

    # difftool
    p = sub.add_parser("difftool", help="Show changes using external/custom diff tool format")
    p.add_argument("from_ref", nargs="?", metavar="REV")
    p.add_argument("to_ref", nargs="?", metavar="REV")
    p.set_defaults(func=cmd_difftool)

    # mergetool
    p = sub.add_parser("mergetool", help="Run merge conflict resolution helper")
    p.set_defaults(func=cmd_mergetool)

    # log
    p = sub.add_parser("log", help="Show commit history")
    p.add_argument("--oneline", action="store_true",
                   help="Condense each commit to a single line")
    p.add_argument("--all", dest="all", action="store_true",
                   help="Show all branches, not only HEAD ancestry")
    p.add_argument("--graph", action="store_true",
                   help="Show an ASCII graph beside each commit")
    p.add_argument("--author", metavar="PATTERN",
                   help="Filter commits by author name or email")
    p.add_argument("--grep", metavar="PATTERN",
                   help="Filter commits by message text")
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
    p.add_argument("--stat", action="store_true",
                   help="Show a diffstat summary instead of full hunks")
    p.add_argument("from_ref", nargs="?", metavar="REV",
                   help="Show diff from this commit/branch")
    p.add_argument("to_ref",   nargs="?", metavar="REV",
                   help="Show diff to this commit/branch (requires FROM_REV)")
    p.set_defaults(func=cmd_diff)

    # show
    p = sub.add_parser("show", help="Show a commit and its diff")
    p.add_argument("target", nargs="?", default="HEAD", metavar="COMMIT")
    p.add_argument("--stat", action="store_true",
                   help="Show a diffstat summary instead of full hunks")
    p.set_defaults(func=cmd_show)

    # ls-files
    p = sub.add_parser("ls-files", help="List files tracked in the index")
    p.add_argument("--stage", action="store_true",
                   help="Show mode and SHA for each entry")
    p.set_defaults(func=cmd_ls_files)

    # blame
    p = sub.add_parser("blame", help="Show per-line authorship for a file")
    p.add_argument("file", metavar="FILE")
    p.set_defaults(func=cmd_blame)

    # branch
    p = sub.add_parser("branch", help="List, create, delete, or rename branches")
    p.add_argument("-d", dest="delete", action="store_true",
                   help="Delete the named branch")
    p.add_argument("-m", dest="move", metavar="NEW",
                   help="Rename the named branch to NEW")
    p.add_argument("name", nargs="?", metavar="NAME")
    p.set_defaults(func=cmd_branch)

    # checkout
    p = sub.add_parser("checkout", help="Switch branches or restore the working tree")
    p.add_argument("-b", dest="create", action="store_true",
                   help="Create the branch and switch to it")
    p.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    p.add_argument("paths", nargs="*", metavar="PATH", help="Paths to restore")
    p.set_defaults(func=cmd_checkout)

    # tag
    p = sub.add_parser("tag", help="List or create lightweight / annotated tags")
    p.add_argument("-a", "--annotate", dest="annotated", action="store_true",
                   help="Create an annotated tag object")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Tag message for annotated tags")
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
    p.add_argument("action", nargs="?", choices=("push", "pop", "list", "show"),
                   default="push")
    p.add_argument("target", nargs="?", metavar="STASH", help="Stash target for show")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Description for stash push")
    p.add_argument("--stat", action="store_true",
                   help="Show diffstat summary for stash show")
    p.set_defaults(func=cmd_stash)

    # clean
    p = sub.add_parser("clean", help="Remove untracked files from working tree")
    p.add_argument("-f", "--force", action="store_true", help="Force clean execution")
    p.add_argument("-d", "--directories", dest="directories", action="store_true",
                   help="Remove untracked directories too")
    p.set_defaults(func=cmd_clean)

    # rev-parse
    p = sub.add_parser("rev-parse", help="Resolve ref or short SHA to full SHA-256")
    p.add_argument("rev", metavar="REV")
    p.set_defaults(func=cmd_rev_parse)

    # fsck
    p = sub.add_parser("fsck", help="Verify database integrity and report dangling objects")
    p.set_defaults(func=cmd_fsck)

    # gc
    p = sub.add_parser("gc", help="Garbage collect unreferenced dangling objects")
    p.add_argument("--no-prune", action="store_true", help="Report dangling objects without deleting")
    p.set_defaults(func=cmd_gc)

    # archive
    p = sub.add_parser("archive", help="Export repository tree snapshot to a zip file")
    p.add_argument("-o", "--output", required=True, metavar="FILE", help="Output filename (.zip)")
    p.add_argument("--format", default="zip", metavar="FMT", help="Archive format (default: zip)")
    p.add_argument("target", nargs="?", default="HEAD", metavar="COMMIT", help="Revision target")
    p.set_defaults(func=cmd_archive)

    # revert
    p = sub.add_parser("revert", help="Revert changes from a previous commit")
    p.add_argument("target", metavar="COMMIT", help="Target commit to revert")
    p.set_defaults(func=cmd_revert)

    # shortlog
    p = sub.add_parser("shortlog", help="Summarize commit history by author")
    p.add_argument("-s", "--summary", action="store_true", help="Suppress commit descriptions, show count only")
    p.add_argument("target", nargs="?", default="HEAD", metavar="COMMIT", help="Revision target")
    p.set_defaults(func=cmd_shortlog)

    # describe
    p = sub.add_parser("describe", help="Give an object a human-readable name based on an available tag")
    p.add_argument("target", nargs="?", default="HEAD", metavar="COMMIT", help="Revision target")
    p.set_defaults(func=cmd_describe)

    # repack
    p = sub.add_parser("repack", help="Pack unpacked objects in a repository")
    p.add_argument("-k", "--keep-loose", action="store_true", help="Do not delete loose objects after repacking")
    p.set_defaults(func=cmd_repack)

    # submodule
    p = sub.add_parser("submodule", help="Initialize, update or inspect submodules")
    sub_sub = p.add_subparsers(dest="subcommand")
    p_add = sub_sub.add_parser("add", help="Add a new submodule")
    p_add.add_argument("url", metavar="URL", help="Repository URL")
    p_add.add_argument("path", nargs="?", metavar="PATH", help="Destination path")
    p_stat = sub_sub.add_parser("status", help="Show submodule status")
    p.set_defaults(func=cmd_submodule)

    # lfs
    p = sub.add_parser("lfs", help="Git Large File Storage (LFS) utilities")
    sub_lfs = p.add_subparsers(dest="subcommand")
    p_tr = sub_lfs.add_parser("track", help="Track a file pattern with Git LFS")
    p_tr.add_argument("pattern", metavar="PATTERN", help="File pattern to track (e.g. '*.bin')")
    p_ls = sub_lfs.add_parser("ls-files", help="List patterns tracked by LFS")
    p.set_defaults(func=cmd_lfs)

    # bundle
    p = sub.add_parser("bundle", help="Create, unpack, and verify bundle files")
    sub_bun = p.add_subparsers(dest="subcommand")
    p_bc = sub_bun.add_parser("create", help="Create a bundle file")
    p_bc.add_argument("file", metavar="FILE", help="Output bundle filename")
    p_bc.add_argument("target", nargs="?", default="HEAD", metavar="COMMIT", help="Revision target")
    p_bv = sub_bun.add_parser("verify", help="Verify a bundle file")
    p_bv.add_argument("file", metavar="FILE", help="Bundle filename to verify")
    p.set_defaults(func=cmd_bundle)

    # worktree
    p = sub.add_parser("worktree", help="Manage multiple working trees")
    sub_wt = p.add_subparsers(dest="subcommand")
    p_wa = sub_wt.add_parser("add", help="Create a new working tree")
    p_wa.add_argument("path", metavar="PATH", help="Target working tree directory")
    p_wa.add_argument("branch", nargs="?", metavar="BRANCH", help="Branch to check out")
    p_wl = sub_wt.add_parser("list", help="List details of each working tree")
    p_wr = sub_wt.add_parser("remove", help="Remove a working tree")
    p_wr.add_argument("path", metavar="PATH", help="Working tree directory to remove")
    p.set_defaults(func=cmd_worktree)

    # verify-pack
    p = sub.add_parser("verify-pack", help="Validate packed Git archive files")
    p.add_argument("-v", "--verbose", action="store_true", help="Show list of objects contained in the packfile")
    p.add_argument("file", metavar="FILE", help="Packfile index (.idx) path")
    p.set_defaults(func=cmd_verify_pack)

    # count-objects
    p = sub.add_parser("count-objects", help="Count unpacked number of objects and their disk consumption")
    p.add_argument("-v", "--verbose", action="store_true", help="Be verbose about objects and disk consumption")
    p.set_defaults(func=cmd_count_objects)

    # verify-commit
    p = sub.add_parser("verify-commit", help="Check GPG signature of commits")
    p.add_argument("target", nargs="?", default="HEAD", metavar="COMMIT", help="Revision target")
    p.set_defaults(func=cmd_verify_commit)

    # graph
    p = sub.add_parser("graph", help="Render ASCII DAG history graph for commits")
    p.add_argument("-n", dest="max_count", type=int, metavar="N", help="Limit number of commits to output")
    p.set_defaults(func=cmd_graph)

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

    # config
    p = sub.add_parser("config", help="Get or set repository configuration")
    p.add_argument("--list", action="store_true", dest="list_all",
                   help="List all configuration entries")
    p.add_argument("--unset", action="store_true",
                   help="Remove the given key")
    p.add_argument("key", nargs="?", metavar="SECTION.KEY",
                   help="Configuration key (e.g. user.name)")
    p.add_argument("value", nargs="?", metavar="VALUE",
                   help="Value to set")
    p.set_defaults(func=cmd_config)

    # grep
    p = sub.add_parser("grep", help="Search file contents for a pattern")
    p.add_argument("pattern", metavar="PATTERN")
    p.add_argument("commit", nargs="?", metavar="COMMIT",
                   help="Search files from a specific commit")
    p.add_argument("-i", "--ignore-case", action="store_true",
                   help="Case-insensitive matching")
    p.add_argument("-n", "--line-number", action="store_true", default=True,
                   help="Show line numbers (default)")
    p.add_argument("-c", "--count", action="store_true",
                   help="Only print a count of matches per file")
    p.set_defaults(func=cmd_grep)

    # notes
    p = sub.add_parser("notes", help="Add or inspect commit notes")
    p.add_argument("action", nargs="?",
                   choices=("add", "show", "list", "remove"),
                   default="list",
                   metavar="add|show|list|remove")
    p.add_argument("-m", "--message", metavar="MSG",
                   help="Note text")
    p.add_argument("commit", nargs="?", default="HEAD", metavar="COMMIT")
    p.set_defaults(func=cmd_notes)

    return parser


def cmd_config(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.list_all:
        for section, key, value in repo.config_list():
            print(f"{section}.{key}={value}")
        return
    if not args.key:
        print("error: key is required", file=sys.stderr)
        sys.exit(1)
    if "." not in args.key:
        print(f"error: key does not contain a section: {args.key}", file=sys.stderr)
        sys.exit(1)
    section, key = args.key.split(".", 1)
    if args.unset:
        repo.config_unset(section, key)
        return
    if args.value is not None:
        repo.config_set(section, key, args.value)
        return
    val = repo.config_get(section, key)
    if val is None:
        sys.exit(1)
    print(val)


def cmd_grep(args: argparse.Namespace) -> None:
    repo = _find_repo()
    results = repo.grep(
        args.pattern,
        target=args.commit,
        ignore_case=args.ignore_case,
        line_number=args.line_number,
        count_only=args.count,
    )
    for line in results:
        print(line)


def cmd_notes(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.action == "add":
        if not args.message:
            print("error: -m MSG is required for 'notes add'", file=sys.stderr)
            sys.exit(1)
        sha = repo.notes_add(args.commit, args.message)
        print(f"Added note {sha[:12]} to {args.commit}")
    elif args.action == "show":
        text = repo.notes_show(args.commit)
        if text is None:
            print(f"No note found for {args.commit}")
        else:
            print(text)
    elif args.action == "remove":
        if repo.notes_remove(args.commit):
            print(f"Removed note from {args.commit}")
        else:
            print(f"No note found for {args.commit}")
    else:
        entries = repo.notes_list()
        if not entries:
            print("No notes found.")
        for commit_sha, note_sha in entries:
            print(f"{commit_sha[:12]}  {note_sha[:12]}")


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
