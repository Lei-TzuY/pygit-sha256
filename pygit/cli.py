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
    br_name = getattr(args, "branch", None)
    s_branch = getattr(args, "single_branch", False)
    repo = Repository.clone(
        args.url, args.directory, depth=args.depth,
        branch_name=br_name, single_branch=s_branch,
    )
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
    repo = _find_repo()
    if args.patch:
        for p in args.pathspec:
            repo.apply_hunk_to_index(p)
            print(f"Staged patch hunk for '{p}'")
    else:
        repo.add(args.pathspec)


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
    topo    = getattr(args, "topo_order", False)
    merges_val = None
    if getattr(args, "merges", False):
        merges_val = True
    elif getattr(args, "no_merges", False):
        merges_val = False

    lr_tuple = None
    lr_arg = getattr(args, "line_range", None)
    if lr_arg:
        if ":" in lr_arg:
            r_part, f_part = lr_arg.rsplit(":", 1)
            if "," in r_part:
                sp, ep = r_part.split(",", 1)
                lr_tuple = (int(sp), int(ep), f_part)
            elif r_part.isdigit():
                lr_tuple = (int(r_part), int(r_part), f_part)

    commits = repo.log(
        max_count=args.n or 0,
        all_branches=args.all,
        author=args.author or None,
        grep=args.grep or None,
        since=args.since or None,
        until=args.until or None,
        patch=args.patch,
        follow=args.follow or None,
        topo_order=topo,
        merges_only=merges_val,
        line_range=lr_tuple,
        first_parent=getattr(args, "first_parent", False),
        min_parents=getattr(args, "min_parents", None),
        max_parents=getattr(args, "max_parents", None),
    )
    if not commits:
        print("(no commits yet)")
        return
    if args.graph:
        print(repo.format_log_graph(commits))
        return
    fmt_str = getattr(args, "format", None)
    for sha, commit in commits:
        if fmt_str:
            print(repo.format_commit(sha, commit, fmt_str))
        elif args.oneline:
            print(f"{sha[:12]} {commit.message.splitlines()[0]}")
        else:
            date_fmt = getattr(args, "date", None)
            print(commit.pretty_print(sha, date_format=date_fmt))
            if args.patch:
                try:
                    diff_text = repo.show(sha)
                    if diff_text:
                        print(diff_text)
                except Exception:
                    pass
            print()


def cmd_status(args: argparse.Namespace) -> None:
    repo   = _find_repo()
    if getattr(args, "short", False):
        if getattr(args, "branch", False):
            result = repo.status()
            b_name = result["branch"] or "HEAD"
            up = result.get("upstream")
            if up:
                ah, bh = result.get("ahead", 0), result.get("behind", 0)
                info = []
                if ah: info.append(f"ahead {ah}")
                if bh: info.append(f"behind {bh}")
                suffix = f" [{', '.join(info)}]" if info else ""
                print(f"## {b_name}...{up}{suffix}")
            else:
                print(f"## {b_name}")
        for line in repo.format_short_status():
            print(line)
        return

    if getattr(args, "porcelain", False):
        result = repo.status(ignored=True)
        if getattr(args, "branch", False):
            b_name = result["branch"] or "HEAD"
            up = result.get("upstream")
            if up:
                ah, bh = result.get("ahead", 0), result.get("behind", 0)
                info = []
                if ah: info.append(f"ahead {ah}")
                if bh: info.append(f"behind {bh}")
                suffix = f" [{', '.join(info)}]" if info else ""
                print(f"## {b_name}...{up}{suffix}")
            else:
                print(f"## {b_name}")
        staged_dict = {p: ("A" if k == "new file" else ("D" if k == "deleted" else "M")) for k, p in result["staged"]}
        unstaged_dict = {p: ("D" if k == "deleted" else "M") for k, p in result["unstaged"]}
        all_paths = sorted(set(staged_dict) | set(unstaged_dict) | set(result["untracked"]) | set(result.get("ignored", [])))
        for p in all_paths:
            x = staged_dict.get(p, " ")
            y = unstaged_dict.get(p, " ")
            if p in result["untracked"]:
                print(f"?? {p}")
            elif p in result.get("ignored", []):
                print(f"!! {p}")
            else:
                print(f"{x}{y} {p}")
        return

    ignored_flag = getattr(args, "ignored", False)
    result = repo.status(ignored=ignored_flag)

    branch = result["branch"] or "HEAD (detached)"
    print(f"On branch {branch}")
    if result.get("operation"):
        print(f"You are currently in a {result['operation']} operation.")
    print()

    if not any(result.get(k) for k in ("staged", "unstaged", "untracked", "ignored", "conflicts")):
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

    if result.get("ignored"):
        print("Ignored files:")
        for path in result["ignored"]:
            print(f"\t{path}")
        print()
        print()


def cmd_diff(args: argparse.Namespace) -> None:
    from_ref = getattr(args, "from_ref", None) or None
    to_ref   = getattr(args, "to_ref",   None) or None
    ns = getattr(args, "name_status", False)
    no = getattr(args, "name_only", False)
    w_space = getattr(args, "ignore_all_space", False)
    b_space = getattr(args, "ignore_space_change", False)
    i_match = getattr(args, "ignore_matching_lines", None)
    c_sum = getattr(args, "compact_summary", False)
    is_raw = getattr(args, "raw", False)
    s_pref = getattr(args, "src_prefix", "a/")
    d_pref = getattr(args, "dst_prefix", "b/")
    n_pref = getattr(args, "no_prefix", False)
    i_sub = getattr(args, "ignore_submodules", False)
    f_ren = getattr(args, "find_renames", False)
    f_cop = getattr(args, "find_copies", False)
    s_mod = getattr(args, "submodule", None)
    d_stat = getattr(args, "dirstat", False)
    g_wid = getattr(args, "stat_graph_width", None)
    output = _find_repo().diff(
        cached=args.cached,
        stat=args.stat,
        from_ref=from_ref,
        to_ref=to_ref,
        name_status=ns,
        name_only=no,
        ignore_all_space=w_space,
        ignore_space_change=b_space,
        ignore_matching_lines=i_match,
        compact_summary=c_sum,
        raw=is_raw,
        src_prefix=s_pref,
        dst_prefix=d_pref,
        no_prefix=n_pref,
        ignore_submodules=i_sub,
        find_renames=f_ren,
        find_copies=f_cop,
        submodule=s_mod,
        dirstat=d_stat,
        stat_graph_width=g_wid,
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
    repo = _find_repo()
    lr = None
    if args.line_range:
        parts = args.line_range.split(",")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            lr = (int(parts[0]), int(parts[1]))
        elif len(parts) == 1 and parts[0].isdigit():
            lr = (int(parts[0]), int(parts[0]))
    lines = repo.blame(args.file, line_range=lr)
    for line in lines:
        print(line)


def cmd_branch(args: argparse.Namespace) -> None:
    repo = _find_repo()
    cnt = getattr(args, "contains", None)
    ncnt = getattr(args, "no_contains", None)
    mrg = getattr(args, "merged", None)
    nmrg = getattr(args, "no_merged", None)
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
        branches = repo.branch(contains=cnt, no_contains=ncnt, merged=mrg, no_merged=nmrg) or []
        current = repo.refs.current_branch()
        for b in branches:
            prefix = "* " if b == current else "  "
            print(f"{prefix}{b}")
        if getattr(args, "all", False):
            for rb in repo.list_remote_branches():
                print(f"  {rb}")


def cmd_show_branch(args: argparse.Namespace) -> None:
    repo = _find_repo()
    print(repo.show_branch())


def cmd_checkout(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.patch and args.paths:
        for p in args.paths:
            repo.apply_hunk_to_worktree(p)
            print(f"Restored patch hunk for '{p}'")
        return
    if args.orphan:
        if not args.target:
            print("error: branch name required for --orphan", file=sys.stderr)
            sys.exit(1)
        repo.checkout(args.target, orphan=True)
        print(f"Switched to a new orphan branch '{args.target}'")
        return
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
        # checkout -b <branch> [start_point]: create branch then switch
        start_pt = getattr(args, "start_point", None) or (args.paths[0] if args.paths else None)
        repo.branch(args.target, start_point=start_pt)
        repo.checkout(args.target)
        print(f"Switched to a new branch '{args.target}'")
        return
    if getattr(args, "detach", False) or (args.target and not repo.refs.get_branch(args.target) and repo.refs.resolve(args.target)):
        target = args.target or "HEAD"
        sha = repo.refs.resolve(target)
        if not sha:
            raise KeyError(f"Unknown revision: '{target}'")
        repo.checkout(target)
        repo.refs.set_head_detached(sha, message=f"checkout: moving to {target}")
        print(f"HEAD is now at {sha[:12]}")
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
    tags_flag = getattr(args, "tags", False)
    always_flag = getattr(args, "always", False)
    desc = repo.describe(target, tags=tags_flag, always=always_flag)
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
    if getattr(args, "dry_run", False):
        st = repo.status()
        b_name = st["branch"] or "HEAD"
        print(f"On branch {b_name}")
        print("Dry run: changes matched for commit, no commit created.")
        return
    msg = args.message or ""
    if args.amend and getattr(args, "no_edit", False) and not msg:
        head_sha = repo.refs.resolve_head()
        if head_sha:
            c_head = repo.store.read(head_sha)
            if hasattr(c_head, "message"):
                msg = c_head.message
    only_p = getattr(args, "only_paths", None)
    inc_p = getattr(args, "include_paths", None)
    a_empty = getattr(args, "allow_empty", False)
    c_up = getattr(args, "cleanup", "strip")
    r_msg = getattr(args, "reuse_message", None)
    re_msg = getattr(args, "reedit_message", None)
    c_date = getattr(args, "date", None)
    r_author = getattr(args, "reset_author", False)
    s_off = getattr(args, "signoff", False)
    sha = repo.commit(
        message=msg,
        author=args.author,
        amend=args.amend,
        template=args.template,
        fixup=args.fixup,
        squash=args.squash,
        only_paths=only_p,
        include_paths=inc_p,
        allow_empty=a_empty,
        cleanup=c_up,
        reuse_message=r_msg,
        reedit_message=re_msg,
        commit_date=c_date,
        reset_author=r_author,
        signoff=s_off,
    )
    quiet = getattr(args, "quiet", False)
    if not quiet:
        label = repo.refs.current_branch() or "HEAD"
        c_obj = repo.store.read(sha)
        subj = c_obj.message.split("\n", 1)[0] if hasattr(c_obj, "message") else ""
        print(f"[{label} {sha[:7]}] {subj}")
    if getattr(args, "verbose", False):
        v_diff = repo.diff(cached=True)
        if v_diff:
            print(v_diff)


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
        import fnmatch as _fnmatch
        tags = repo.tag() or []
        pattern = getattr(args, "list_pattern", None)
        if pattern:
            tags = [t for t in tags if _fnmatch.fnmatch(t, pattern)]
        for t in tags:
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
    squash = getattr(args, "squash", False)
    result = repo.merge(args.target, message=args.message, squash=squash)
    status = result["status"]
    if status == "up-to-date":
        print("Already up to date.")
    elif status == "fast-forward":
        print(f"Fast-forward to {str(result['sha'])[:12]}")
    elif status == "merged":
        print(f"Merge made commit {str(result['sha'])[:12]}")
    elif status == "squashed":
        print(f"Squash commit -- not updating HEAD")
        print(f"Changes are staged; use 'pygit commit' to create a single commit.")
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
        result = repo.cherry_pick(args.target, no_commit=args.no_commit)
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
        result = repo.rebase(args.target, autosquash=args.autosquash)
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
    if args.action in ("push", "save"):
        include_u = getattr(args, "include_untracked", False)
        k_idx = getattr(args, "keep_index", False)
        s_only = getattr(args, "staged", False)
        sha = repo.stash_push(args.message or "WIP on current branch", include_untracked=include_u, keep_index=k_idx, staged_only=s_only)
        print(f"Saved working directory and index state {sha[:12]}")
    elif args.action == "create":
        sha = repo.stash_create(message=args.message or "WIP on current branch")
        if sha:
            print(sha)
    elif args.action == "store":
        if not args.target:
            print("error: commit SHA required for 'stash store'", file=sys.stderr)
            sys.exit(1)
        sha = repo.stash_store(args.target, message=args.message or "WIP on current branch")
        print(f"Stored {sha[:12]} in refs/stash")
    elif args.action == "pop":
        sha = repo.stash_pop()
        print(f"Dropped refs/stash@{{0}} ({sha[:12]})")
    elif args.action == "apply":
        idx = int(args.target) if args.target and args.target.isdigit() else 0
        restore_idx = getattr(args, "restore_index", False)
        sha = repo.stash_apply(index=idx, restore_index=restore_idx)
        print(f"Applied stash@{{{idx}}} ({sha[:12]})")
    elif args.action == "drop":
        idx = int(args.target) if args.target and args.target.isdigit() else 0
        sha = repo.stash_drop(index=idx)
        print(f"Dropped stash@{{{idx}}} ({sha[:12]})")
    elif args.action == "branch":
        if not args.target:
            print("error: branch name required for 'stash branch'", file=sys.stderr)
            sys.exit(1)
        sha = repo.stash_branch(args.target, index=0)
        print(f"Switched to a new branch '{args.target}' with stash@{{0}} applied and dropped ({sha[:12]})")
    elif args.action == "clear":
        repo.stash_clear()
        print("Cleared all stash entries.")
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
    p_fmt = getattr(args, "path_format", None)
    if p_fmt:
        r = _find_repo()
        print(str(r.pygit_dir.resolve()) if p_fmt == "absolute" else ".pygit")
        return
    res_dir = getattr(args, "resolve_git_dir", None)
    if res_dir:
        p = Path(res_dir).resolve()
        if (p / ".pygit").is_dir():
            print((p / ".pygit").resolve())
        elif (p / "HEAD").is_file():
            print(p)
        else:
            try:
                r = _find_repo()
                print(r.pygit_dir.resolve())
            except Exception:
                sys.exit(1)
        return
    if getattr(args, "is_shallow_repository", False):
        try:
            r = _find_repo()
            print("true" if (r.pygit_dir / "shallow").exists() else "false")
        except Exception:
            print("false")
        return
    if getattr(args, "is_bare_repository", False):
        print("false")
        return
    if getattr(args, "is_inside_git_dir", False):
        cwd = Path.cwd().resolve()
        inside = False
        try:
            r = _find_repo()
            pg_dir = r.pygit_dir.resolve()
            inside = cwd == pg_dir or pg_dir in cwd.parents
        except Exception:
            pass
        print("true" if inside else "false")
        return
    repo = _find_repo()
    if getattr(args, "abbrev_ref", False):
        target = getattr(args, "target", None) or "HEAD"
        if target == "HEAD":
            head_val = repo.refs.get_head()
            if head_val.startswith("ref: "):
                target = head_val[5:].strip()
        if target.startswith("refs/heads/"):
            print(target[11:])
        elif target.startswith("refs/remotes/"):
            print(target[13:])
        elif target.startswith("refs/tags/"):
            print(target[10:])
        else:
            print(target)
        return
    default_ref = getattr(args, "default", None)
    if not getattr(args, "target", None) and default_ref:
        args.target = default_ref
    s_len = getattr(args, "short", None)
    if s_len is not None and s_len is not False:
        length = int(s_len) if isinstance(s_len, int) or (isinstance(s_len, str) and s_len.isdigit()) else 7
        target = getattr(args, "target", None) or "HEAD"
        res = repo.rev_parse(target)
        print(res[:length])
        return
    if getattr(args, "verify", False):
        target = getattr(args, "target", None)
        if not target:
            print("fatal: Needed a single revision", file=sys.stderr)
            sys.exit(1)
        try:
            res = repo.rev_parse(target)
            print(res)
        except Exception:
            print(f"fatal: Needed a single revision", file=sys.stderr)
            sys.exit(1)
        return
    if getattr(args, "not_flag", False) or getattr(args, "not", False):
        target = getattr(args, "target", None)
        if target:
            res = repo.rev_parse(target)
            print(f"^{res}")
        return
    if getattr(args, "sq", False):
        target = getattr(args, "target", None)
        if target:
            try:
                res = repo.rev_parse(target)
                print("'" + res.replace("'", "'\\''") + "'")
            except Exception:
                print("'" + target.replace("'", "'\\''") + "'")
        return
    revs_only = getattr(args, "revs_only", False)
    no_revs = getattr(args, "no_revs", False)
    if revs_only or no_revs:
        target = getattr(args, "target", None)
        if target:
            try:
                resolved = repo.rev_parse(target)
                if revs_only:
                    print(resolved)
            except Exception:
                if no_revs:
                    print(target)
        return
    if getattr(args, "prefix", False):
        curr = Path.cwd()
        try:
            rel = curr.relative_to(repo.worktree)
            print(f"{rel.as_posix()}/" if str(rel) != "." else "")
        except Exception:
            print("")
        return
    if getattr(args, "is_inside_work_tree", False):
        print("true")
        return
    if getattr(args, "is_shallow_repository", False):
        is_shallow = (repo.pygit_dir / "shallow").exists()
        print("true" if is_shallow else "false")
        return
    if getattr(args, "is_bare_repository", False):
        print("false")
        return
    if getattr(args, "git_dir", False):
        print(str(repo.pygit_dir))
        return
    if getattr(args, "show_toplevel", False):
        print(str(repo.worktree))
        return
    brs = getattr(args, "branches", False)
    tgs = getattr(args, "tags", False)
    rms = getattr(args, "remotes", False)
    if brs or tgs or rms:
        shas = repo.rev_parse_namespaces(branches=brs, tags=tgs, remotes=rms)
        for sha in shas:
            print(sha)
        return
    if not args.rev:
        raise RuntimeError("rev-parse requires a revision or option flag")
    s_full = getattr(args, "symbolic_full_name", False)
    sha = repo.rev_parse(args.rev, symbolic_full_name=s_full)
    if getattr(args, "short", False):
        length = getattr(args, "short_len", 12) or 12
        print(sha[:length])
    else:
        print(sha)


def cmd_mv(args: argparse.Namespace) -> None:
    repo = _find_repo()
    repo.mv(args.source, args.destination, force=args.force)
    print(f"Renamed '{args.source}' -> '{args.destination}'")


def cmd_ls_tree(args: argparse.Namespace) -> None:
    repo = _find_repo()
    lines = repo.ls_tree(
        tree_ish=args.tree_ish,
        recursive=args.recursive,
        name_only=args.name_only,
    )
    for line in lines:
        print(line)


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
    repo = _find_repo()
    if getattr(args, "patch", False):
        cnt = repo.reset_patch(paths=args.pathspec or None)
        print(f"Unstaged {cnt} file hunk(s).")
        return
    mode = "hard" if args.hard else "soft" if args.soft else "mixed"
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
    p.add_argument("-b", "--branch", metavar="BRANCH", help="Point HEAD to specified branch after cloning")
    p.add_argument("--single-branch", action="store_true", help="Clone only the history leading to the tip of a single branch")
    p.add_argument("--depth", type=int, metavar="DEPTH", help="Create a shallow clone with truncated depth")
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
    p.add_argument("-p", "--patch", action="store_true", help="Interactively stage patch hunks")
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
    p.add_argument("-m", "--message", metavar="MSG", help="Commit message")
    p.add_argument("-C", "--reuse-message", metavar="COMMIT", help="Reuse message from specified commit")
    p.add_argument("-c", "--reedit-message", metavar="COMMIT", help="Reuse and edit message from specified commit")
    p.add_argument("--author", metavar="AUTHOR", help="Override author name/email")
    p.add_argument("--date", metavar="DATE", help="Override author and committer timestamp")
    p.add_argument("--reset-author", action="store_true", help="Reset author timestamp and identity when amending")
    p.add_argument("-e", "--edit", action="store_true", help="Force entry into commit message editor")
    p.add_argument("--no-edit", action="store_true", help="Use selected commit message without launching editor")
    p.add_argument("-v", "--verbose", action="store_true", help="Show unified diff of changes to be committed")
    p.add_argument("-q", "--quiet", action="store_true", help="Suppress commit summary message")
    p.add_argument("-s", "--signoff", action="store_true", help="Add Signed-off-by line at the end of the commit message")
    p.add_argument("--dry-run", action="store_true", help="Do not create commit, only show what would be committed")
    p.add_argument("--no-status", action="store_true", help="Do not include status in commit message template")
    p.add_argument("--amend", action="store_true", help="Amend the previous commit")
    p.add_argument("--allow-empty", action="store_true", help="Allow recording a commit with an unchanged tree")
    p.add_argument("--cleanup", choices=["strip", "whitespace", "verbatim"], default="strip", help="Clean up commit message")
    p.add_argument("-t", "--template", metavar="TEMPLATE",
                   help="Use contents of template file as commit message")
    p.add_argument("--fixup", metavar="COMMIT",
                   help="Construct a fixup commit for the target commit")
    p.add_argument("--squash", metavar="COMMIT",
                   help="Construct a squash commit for the target commit")
    p.add_argument("-o", "--only", dest="only_paths", nargs="+", metavar="PATH",
                   help="Commit only specified paths")
    p.add_argument("-i", "--include", dest="include_paths", nargs="+", metavar="PATH",
                   help="Stage specified paths before committing")
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
    p.add_argument("--topo-order", action="store_true",
                   help="Sort commits topologically (children before parents)")
    p.add_argument("--first-parent", action="store_true",
                   help="Follow only the first parent commit upon seeing a merge commit")
    p.add_argument("--min-parents", type=int, metavar="N", help="Show only commits with at least N parents")
    p.add_argument("--max-parents", type=int, metavar="N", help="Show only commits with at most N parents")
    p.add_argument("--date", choices=["relative", "short", "iso"], help="Format commit dates")
    mg = p.add_mutually_exclusive_group()
    mg.add_argument("--merges", action="store_true", help="Print only merge commits")
    mg.add_argument("--no-merges", action="store_true", help="Do not print commits with more than one parent")
    p.add_argument("-L", dest="line_range", metavar="START,END:FILE",
                   help="Trace evolution of line range in file")
    p.add_argument("--author", metavar="PATTERN",
                   help="Filter commits by author name or email")
    p.add_argument("--grep", metavar="PATTERN",
                   help="Filter commits by message text")
    p.add_argument("--since", metavar="DATE",
                   help="Show commits more recent than a specific date")
    p.add_argument("--until", metavar="DATE",
                   help="Show commits older than a specific date")
    p.add_argument("-p", "--patch", action="store_true",
                   help="Generate diff patch for each commit")
    p.add_argument("--follow", metavar="FILE",
                   help="Trace history of a file across renames")
    p.add_argument("-n", type=int, default=0, metavar="N",
                   help="Limit to N most recent commits")
    p.add_argument("--format", metavar="FORMAT",
                   help="Pretty-print each commit with custom format (%%H, %%h, %%an, %%ae, %%s, %%b, %%d)")
    p.set_defaults(func=cmd_log)

    # status
    p = sub.add_parser("status", help="Show working tree status")
    p.add_argument("-s", "--short", action="store_true", help="Give output in short-format")
    p.add_argument("-b", "--branch", action="store_true", help="Show branch and tracking info even in short-format")
    p.add_argument("--ahead-behind", action="store_true", default=True, help="Compute ahead/behind counts for status")
    p.add_argument("--no-ahead-behind", dest="ahead_behind", action="store_false", help="Do not compute ahead/behind counts")
    p.add_argument("--display-comment-prefix", action="store_true", help="Include comment prefix in status template")
    p.add_argument("--porcelain", action="store_true", help="Give output in easy-to-parse format")
    p.add_argument("--ignored", action="store_true", help="Show ignored files")
    p.set_defaults(func=cmd_status)

    # diff
    p = sub.add_parser("diff", help="Show changes between commits, commit and working tree, etc")
    p.add_argument("--cached", "--staged", dest="cached", action="store_true",
                   help="Show diff between index and HEAD")
    p.add_argument("--stat", action="store_true", help="Show diffstat summary")
    p.add_argument("--compact-summary", action="store_true", help="Show compact diffstat summary")
    p.add_argument("--raw", action="store_true", help="Generate raw diff output format")
    p.add_argument("--src-prefix", metavar="PREFIX", default="a/", help="Show given source prefix instead of a/")
    p.add_argument("--dst-prefix", metavar="PREFIX", default="b/", help="Show given destination prefix instead of b/")
    p.add_argument("--no-prefix", action="store_true", help="Do not show any source or destination prefix")
    p.add_argument("--ignore-submodules", action="store_true", help="Ignore changes to submodules in the diff")
    p.add_argument("-M", "--find-renames", action="store_true", help="Detect renames in diff")
    p.add_argument("-C", "--find-copies", action="store_true", help="Detect copies in diff")
    p.add_argument("--submodule", nargs="?", const="short", help="Specify how differences in submodules are shown")
    p.add_argument("--dirstat", action="store_true", help="Output distribution of relative amount of changes for each directory")
    p.add_argument("--stat-graph-width", type=int, metavar="WIDTH", help="Limit diffstat graph width")
    p.add_argument("--ws-error-highlight", metavar="KIND", help="Highlight whitespace errors in diff")
    p.add_argument("--stat-width", type=int, metavar="WIDTH", help="Limit diffstat summary width")
    p.add_argument("--name-status", action="store_true", help="Show status and path only")
    p.add_argument("--name-only", action="store_true", help="Show file paths only")
    p.add_argument("-w", "--ignore-all-space", action="store_true", help="Ignore all whitespace")
    p.add_argument("-b", "--ignore-space-change", action="store_true", help="Ignore space count changes")
    p.add_argument("-I", "--ignore-matching-lines", metavar="REGEX", help="Ignore changes whose lines all match REGEX")
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
    p.add_argument("-L", dest="line_range", metavar="START,END", help="Process only line range n,m")
    p.add_argument("file", metavar="FILE")
    p.set_defaults(func=cmd_blame)

    # branch
    p = sub.add_parser("branch", help="List, create, or delete branches")
    p.add_argument("-a", "--all", dest="all", action="store_true", help="List remote branches too")
    p.add_argument("-d", "--delete", action="store_true", help="Delete branch")
    p.add_argument("-m", "--move", metavar="NEW", help="Rename branch")
    p.add_argument("--contains", metavar="COMMIT", help="Only list branches which contain specified commit")
    p.add_argument("--no-contains", metavar="COMMIT", help="Only list branches which do not contain specified commit")
    p.add_argument("--merged", nargs="?", const="HEAD", metavar="COMMIT", help="Only list branches merged into specified commit")
    p.add_argument("--no-merged", nargs="?", const="HEAD", metavar="COMMIT", help="Only list branches not merged into specified commit")
    p.add_argument("name", nargs="?", metavar="BRANCH")
    p.set_defaults(func=cmd_branch)

    # show-branch
    p = sub.add_parser("show-branch", help="Show branches and their commits")
    p.set_defaults(func=cmd_show_branch)

    # checkout
    p = sub.add_parser("checkout", help="Switch branches or restore the working tree")
    p.add_argument("-b", dest="create", action="store_true",
                   help="Create the branch and switch to it")
    p.add_argument("--detach", action="store_true",
                   help="Detach HEAD at named commit")
    p.add_argument("--orphan", action="store_true",
                   help="Create a new orphan branch with no commits")
    p.add_argument("-p", "--patch", action="store_true",
                   help="Interactively restore patch hunks to working tree")
    p.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    p.add_argument("start_point", nargs="?", metavar="START_POINT", help="Start point for new branch creation")
    p.add_argument("paths", nargs="*", metavar="PATH", help="Paths to restore")
    p.set_defaults(func=cmd_checkout)

    # tag
    p = sub.add_parser("tag", help="List or create lightweight / annotated tags")
    p.add_argument("-a", "--annotate", dest="annotated", action="store_true",
                   help="Create an annotated tag object")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Tag message for annotated tags")
    p.add_argument("-l", dest="list_pattern", metavar="PATTERN",
                   help="List tags matching a glob pattern")
    p.add_argument("name",   nargs="?", metavar="NAME")
    p.add_argument("commit", nargs="?", metavar="COMMIT",
                   help="Commit to tag (default: HEAD)")
    p.set_defaults(func=cmd_tag)

    # merge
    p = sub.add_parser("merge", help="Join another development history")
    merge_action = p.add_mutually_exclusive_group()
    merge_action.add_argument("--abort", action="store_true",
                              help="Abort an in-progress conflicted merge")
    p.add_argument("--squash", action="store_true",
                   help="Squash merge: apply changes without creating merge commit")
    p.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Override the merge commit message")
    p.set_defaults(func=cmd_merge)

    # cherry-pick
    p = sub.add_parser("cherry-pick", help="Replay a commit on top of HEAD")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--continue", dest="continue_", action="store_true",
                        help="Continue after resolving conflicts")
    action.add_argument("--abort", action="store_true",
                        help="Abort and restore the original branch")
    p.add_argument("-n", "--no-commit", action="store_true",
                   help="Apply changes without committing")
    p.add_argument("target", nargs="?", metavar="COMMIT")
    p.set_defaults(func=cmd_cherry_pick)

    # rebase
    p = sub.add_parser("rebase", help="Reapply commits on top of another base tip")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--continue", dest="continue_", action="store_true",
                        help="Continue after resolving conflicts")
    action.add_argument("--skip", action="store_true",
                        help="Skip the currently stopped commit")
    action.add_argument("--abort", action="store_true",
                        help="Abort and restore the original branch")
    p.add_argument("--autosquash", action="store_true",
                   help="Automatically reorder fixup!/squash! commits")
    p.add_argument("target", nargs="?", metavar="BRANCH|SHA")
    p.set_defaults(func=cmd_rebase)

    # bisect
    p = sub.add_parser("bisect", help="Find the first bad commit by binary search")
    p.add_argument("action", choices=("start", "good", "bad", "reset"))
    p.add_argument("revisions", nargs="*", metavar="REV")
    p.set_defaults(func=cmd_bisect)

    # stash
    p = sub.add_parser("stash", help="Stash changes in a dirty working directory")
    p.add_argument("action", nargs="?", choices=["push", "save", "pop", "apply", "drop", "clear", "create", "store", "branch", "list", "show"], default="push")
    p.add_argument("target", nargs="?", metavar="STASH", help="Stash target or branch name")
    p.add_argument("-m", dest="message", metavar="MSG",
                   help="Description for stash push")
    p.add_argument("-u", "--include-untracked", action="store_true",
                   help="Include untracked files in stash")
    p.add_argument("-k", "--keep-index", action="store_true",
                   help="All changes already added to the index are left intact in working tree and index")
    p.add_argument("--index", dest="restore_index", action="store_true",
                   help="Reinstate staged changes as well as working tree changes")
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
    p = sub.add_parser("rev-parse", help="Parse revision parameters")
    p.add_argument("--path-format", choices=["absolute", "relative"], help="Specify path format for rev-parse output")
    p.add_argument("--resolve-git-dir", metavar="PATH", help="Check if PATH is a valid git repository or gitfile")
    p.add_argument("--is-shallow-repository", action="store_true", help="Check if repository is shallow clone")
    p.add_argument("--is-bare-repository", action="store_true", help="Check if repository is bare")
    p.add_argument("--is-inside-git-dir", action="store_true", help="Check if current directory is inside control directory")
    p.add_argument("--abbrev-ref", action="store_true", help="Output abbreviated name of reference")
    p.add_argument("--verify", action="store_true", help="Verify that object exists")
    p.add_argument("--default", metavar="ARG", help="Use ARG if target parameter is missing")
    p.add_argument("--symbolic-full-name", action="store_true", help="Output full ref name")
    p.add_argument("--is-inside-work-tree", action="store_true", help="Check if inside working tree")
    p.add_argument("--is-shallow-repository", action="store_true", help="Check if repository is shallow clone")
    p.add_argument("--is-bare-repository", action="store_true", help="Check if repository is bare")
    p.add_argument("--prefix", action="store_true", help="Show relative path from working tree root")
    p.add_argument("--sq", action="store_true", help="Single-quote output for shell eval")
    p.add_argument("--not", dest="not_flag", action="store_true", help="Prefix output with ^")
    p.add_argument("--revs-only", action="store_true", help="Do not output flags or non-revision arguments")
    p.add_argument("--no-revs", action="store_true", help="Do not output revision arguments")
    p.add_argument("--git-dir", action="store_true", help="Print path to .pygit directory")
    p.add_argument("--show-toplevel", action="store_true", help="Print absolute path of top-level worktree")
    p.add_argument("--symbolic-full-name", action="store_true", help="Print full ref path (e.g. refs/heads/main)")
    p.add_argument("--branches", action="store_true", help="Show all branch refs")
    p.add_argument("--tags", action="store_true", help="Show all tag refs")
    p.add_argument("--remotes", action="store_true", help="Show all remote refs")
    p.add_argument("--verify", action="store_true", help="Verify revision parameter")
    p.add_argument("--short", action="store_true", help="Print short SHA")
    p.add_argument("rev", nargs="?", metavar="REV")
    p.set_defaults(func=cmd_rev_parse)

    # mv
    p = sub.add_parser("mv", help="Move or rename a file, directory, or symlink")
    p.add_argument("-f", "--force", action="store_true", help="Force move even if destination exists")
    p.add_argument("source", metavar="SOURCE")
    p.add_argument("destination", metavar="DESTINATION")
    p.set_defaults(func=cmd_mv)

    # ls-tree
    p = sub.add_parser("ls-tree", help="List the contents of a tree object")
    p.add_argument("-r", "--recursive", dest="recursive", action="store_true", help="Recurse into sub-trees")
    p.add_argument("--name-only", action="store_true", help="List only filenames")
    p.add_argument("tree_ish", nargs="?", default="HEAD", metavar="TREE-ISH")
    p.set_defaults(func=cmd_ls_tree)

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
    p.add_argument("--tags", action="store_true", help="Use any tag, even lightweight tags")
    p.add_argument("--always", action="store_true", help="Show uniquely abbreviated commit object as fallback")
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
    p.add_argument("-p", "--patch", action="store_true", help="Interactively unstage diff hunks")
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

    # sparse-checkout
    p = sub.add_parser("sparse-checkout", help="Manage sparse checkout rules")
    p.add_argument("action", nargs="?", choices=("set", "list", "disable"), default="list", metavar="set|list|disable")
    p.add_argument("patterns", nargs="*", metavar="PATTERN")
    p.set_defaults(func=cmd_sparse_checkout)

    # commit-graph
    p = sub.add_parser("commit-graph", help="Write binary commit-graph acceleration file")
    sub_cg = p.add_subparsers(dest="subcommand")
    p_cgw = sub_cg.add_parser("write", help="Write commit-graph file")
    p.set_defaults(func=cmd_commit_graph)

    # rerere
    p = sub.add_parser("rerere", help="Reuse recorded resolution of conflicted merges")
    p.add_argument("action", nargs="?", choices=("status", "forget"), default="status")
    p.set_defaults(func=cmd_rerere)

    # write-tree
    p = sub.add_parser("write-tree", help="Create a tree object from the current index")
    p.set_defaults(func=cmd_write_tree)

    # commit-tree
    p = sub.add_parser("commit-tree", help="Create a new commit object from a tree")
    p.add_argument("tree", metavar="TREE")
    p.add_argument("-p", dest="parent", metavar="PARENT", help="Parent commit SHA")
    p.add_argument("-m", dest="message", default="commit-tree commit", metavar="MSG", help="Commit message")
    p.set_defaults(func=cmd_commit_tree)

    # maintenance
    p = sub.add_parser("maintenance", help="Run repository maintenance and optimization pipeline")
    p.add_argument("action", nargs="?", choices=("run",), default="run")
    p.set_defaults(func=cmd_maintenance)

    # check-ignore
    p = sub.add_parser("check-ignore", help="Debug gitignore / exclude files")
    p.add_argument("pathspec", nargs="+", metavar="PATH")
    p.set_defaults(func=cmd_check_ignore)

    # update-ref
    p = sub.add_parser("update-ref", help="Update the object name stored in a ref safely")
    p.add_argument("ref", metavar="REF")
    p.add_argument("new_sha", metavar="NEW_SHA")
    p.add_argument("old_sha", nargs="?", metavar="OLD_SHA")
    p.set_defaults(func=cmd_update_ref)

    # symbolic-ref
    p = sub.add_parser("symbolic-ref", help="Read, modify and delete symbolic refs")
    p.add_argument("name", nargs="?", default="HEAD", metavar="NAME")
    p.add_argument("target", nargs="?", metavar="TARGET")
    p.set_defaults(func=cmd_symbolic_ref)

    # filter-branch
    p = sub.add_parser("filter-branch", help="Rewrite branch history")
    p.add_argument("--path", required=True, metavar="PREFIX", help="Keep only paths matching prefix")
    p.add_argument("branch", nargs="?", metavar="BRANCH")
    p.set_defaults(func=cmd_filter_branch)

    # rev-list
    p = sub.add_parser("rev-list", help="List commit objects in reverse chronological order")
    p.add_argument("commit", nargs="?", default="HEAD", metavar="COMMIT")
    p.add_argument("--count", action="store_true", help="Print only the count of commits")
    p.add_argument("--left-right", action="store_true", help="Mark which side of a symmetric difference commit was reachable from")
    p.add_argument("-n", type=int, default=0, metavar="N", help="Limit number of commits to list")
    p.set_defaults(func=cmd_rev_list)

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


def cmd_sparse_checkout(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.action == "set":
        if not args.patterns:
            print("error: patterns required for 'sparse-checkout set'", file=sys.stderr)
            sys.exit(1)
        repo.sparse_checkout_set(args.patterns)
        print("Updated sparse checkout patterns.")
    elif args.action == "disable":
        repo.sparse_checkout_disable()
        print("Disabled sparse checkout.")
    else:
        patterns = repo.sparse_checkout_list()
        if not patterns:
            print("Sparse checkout is not enabled.")
        else:
            for p in patterns:
                print(p)


def cmd_commit_graph(args: argparse.Namespace) -> None:
    repo = _find_repo()
    path = repo.write_commit_graph()
    print(f"Wrote commit-graph to {path}")


def cmd_rerere(args: argparse.Namespace) -> None:
    repo = _find_repo()
    statuses = repo.rerere_status()
    if not statuses:
        print("There are no recorded resolutions.")
    else:
        for chash, st in statuses:
            print(f"{chash[:12]}  {st}")


def cmd_write_tree(args: argparse.Namespace) -> None:
    repo = _find_repo()
    tree_sha = repo._build_tree()
    print(tree_sha)


def cmd_commit_tree(args: argparse.Namespace) -> None:
    repo = _find_repo()
    from pygit.objects import CommitObject
    from pygit.objects.commit import Identity
    identity = Identity("Plumbing", "plumbing@example.com")
    parents = [args.parent] if args.parent else []
    c_obj = CommitObject(
        tree=args.tree,
        parents=parents,
        author=identity,
        committer=identity,
        message=args.message,
    )
    sha = repo.store.write(c_obj)
    print(sha)


def cmd_maintenance(args: argparse.Namespace) -> None:
    repo = _find_repo()
    info = repo.maintenance()
    print(f"Maintenance complete. Repacked: {info['pack'][:12]}, Graph: {info['commit_graph']}, Pruned: {info['pruned']}")


def cmd_check_ignore(args: argparse.Namespace) -> None:
    repo = _find_repo()
    matches = repo.check_ignore(args.pathspec)
    for path, pattern, source in matches:
        print(f"{source}: {pattern}\t{path}")


def cmd_update_ref(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.old_sha:
        current = repo.refs.resolve(args.ref)
        if current != args.old_sha:
            print(f"error: {args.ref} does not match {args.old_sha}", file=sys.stderr)
            sys.exit(1)
    if args.ref.startswith("refs/heads/"):
        branch_name = args.ref[11:]
        repo.refs.set_branch(branch_name, args.new_sha, message="update-ref")
    else:
        repo.refs.set_head_detached(args.new_sha, message="update-ref")
    print(f"Updated {args.ref} to {args.new_sha[:12]}")


def cmd_symbolic_ref(args: argparse.Namespace) -> None:
    repo = _find_repo()
    if args.target:
        target_branch = args.target[11:] if args.target.startswith("refs/heads/") else args.target
        repo.refs.set_head_symbolic(target_branch, message="symbolic-ref")
        print(f"Updated {args.name} to point to refs/heads/{target_branch}")
    else:
        target = repo.refs.current_branch()
        if target:
            print(f"refs/heads/{target}")
        else:
            sha = repo.refs.resolve_head()
            print(sha)


def cmd_filter_branch(args: argparse.Namespace) -> None:
    repo = _find_repo()
    new_sha = repo.filter_branch(args.path, branch_name=args.branch)
    print(f"Rewrote history. New tip SHA: {new_sha[:12]}")


def cmd_rev_list(args: argparse.Namespace) -> None:
    repo = _find_repo()
    l_right = getattr(args, "left_right", False)
    if "..." in args.commit and l_right:
        left_ref, right_ref = args.commit.split("...", 1)
        left_commits = {sha for sha, _ in repo.log(start=left_ref)}
        right_commits = {sha for sha, _ in repo.log(start=right_ref)}
        sym_diff = (left_commits ^ right_commits)
        if args.count:
            print(len(sym_diff))
            return
        for sha in left_commits - right_commits:
            print(f"<{sha}")
        for sha in right_commits - left_commits:
            print(f">{sha}")
        return

    commits = repo.log(start=args.commit, max_count=args.n or 0)
    if args.count:
        print(len(commits))
    else:
        for sha, _ in commits:
            print(sha)


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
