"""Resolve Git-style default push refspecs and safety rules."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from .remote_ops import configured_upstream
from .repo import Repository


@dataclass(frozen=True)
class PushSpec:
    """One local branch -> remote branch update."""

    source: str
    target: str
    force: bool = False


@dataclass(frozen=True)
class PushPlan:
    """Resolved no-refspec/explicit-refspec push operation."""

    remote: str
    specs: Tuple[PushSpec, ...]
    mode: str
    auto_setup_upstream: bool = False


def _true(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _current_branch(repo: Repository) -> str:
    branch = repo.refs.current_branch()
    if not branch:
        raise RuntimeError("cannot choose a push refspec from detached HEAD")
    return branch


def _strip_heads(value: str, *, field: str) -> str:
    if value.startswith("refs/heads/"):
        value = value[len("refs/heads/") :]
    elif value.startswith("refs/"):
        raise RuntimeError(f"Phase166 push refspecs only support branch {field}s: '{value}'")
    if not value:
        raise RuntimeError(f"empty push {field} branch is not supported")
    return value


def _source_branch(repo: Repository, value: str) -> str:
    if value == "HEAD":
        return _current_branch(repo)
    branch = _strip_heads(value, field="source")
    if not repo.refs.get_branch(branch):
        raise KeyError(f"Unknown local branch: '{branch}'")
    return branch


def parse_push_refspec(repo: Repository, text: str) -> Tuple[PushSpec, ...]:
    """Parse the branch-only subset of Git push refspec syntax.

    Supports ``branch``, ``HEAD``, ``src:dst``, a leading ``+`` force marker,
    and the special ``:`` / ``+:`` matching forms. Deletion refspecs, tags,
    arbitrary revision expressions, and wildcard refspecs are intentionally
    left for later phases.
    """

    raw = text.strip()
    if not raw:
        raise RuntimeError("empty push refspec")
    force = raw.startswith("+")
    body = raw[1:] if force else raw
    if body == ":":
        return (PushSpec(":", ":", force=force),)
    if "*" in body or body.startswith(":") or body.endswith(":"):
        raise RuntimeError(f"unsupported push refspec in Phase166: '{text}'")

    if ":" in body:
        source_text, target_text = body.split(":", 1)
        if ":" in target_text:
            raise RuntimeError(f"invalid push refspec: '{text}'")
        source = _source_branch(repo, source_text)
        target = _strip_heads(target_text, field="destination")
    else:
        source = _source_branch(repo, body)
        target = source
    return (PushSpec(source, target, force=force),)


def _matching_specs(repo: Repository, remote: str, *, force: bool = False) -> Tuple[PushSpec, ...]:
    local = set(repo.refs.list_branches())
    remote_names = set(repo.refs.list_remotes(remote))
    return tuple(
        PushSpec(branch, branch, force=force)
        for branch in sorted(local & remote_names)
    )


def _expand_refspecs(
    repo: Repository,
    remote: str,
    values: Iterable[str],
) -> Tuple[PushSpec, ...]:
    specs = []
    for value in values:
        parsed = parse_push_refspec(repo, value)
        if len(parsed) == 1 and parsed[0].source == parsed[0].target == ":":
            specs.extend(_matching_specs(repo, remote, force=parsed[0].force))
        else:
            specs.extend(parsed)
    return tuple(specs)


def _configured_push_refspecs(repo: Repository, remote: str) -> Tuple[str, ...]:
    value = repo.config_get("remote", f"{remote}.push")
    if not value:
        return ()
    # The repository's educational config layer stores one scalar value per
    # key. Accept shell-style whitespace to make that scalar useful for one or
    # more branch refspecs while keeping deterministic parsing.
    return tuple(shlex.split(value))


def _default_fetch_remote(repo: Repository) -> Optional[str]:
    remotes = repo.list_remotes()
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return next(iter(remotes))
    return None


def push_default(repo: Repository) -> str:
    value = (repo.config_get("push", "default") or "simple").strip().lower()
    if value == "tracking":
        value = "upstream"
    if value not in {"nothing", "current", "upstream", "simple", "matching"}:
        raise RuntimeError(f"unsupported push.default value: '{value}'")
    return value


def resolve_push_plan(
    repo: Repository,
    remote: str,
    refspecs: Sequence[str] = (),
) -> PushPlan:
    """Resolve destination branch(es) for one push invocation.

    Precedence follows Git: command-line refspecs, then ``remote.<name>.push``,
    then ``push.default``. ``push.autoSetupRemote`` is carried in the plan so
    the CLI can persist tracking after a successful default push.
    """

    current = _current_branch(repo)
    explicit = tuple(refspecs)
    if explicit:
        return PushPlan(remote, _expand_refspecs(repo, remote, explicit), "explicit")

    configured = _configured_push_refspecs(repo, remote)
    if configured:
        return PushPlan(remote, _expand_refspecs(repo, remote, configured), "remote.push")

    mode = push_default(repo)
    if mode == "nothing":
        raise RuntimeError(
            'no refspec was specified and push.default is "nothing"'
        )
    if mode == "matching":
        return PushPlan(remote, _matching_specs(repo, remote), mode)

    upstream = configured_upstream(repo, current)
    auto_setup = _true(repo.config_get("push", "autoSetupRemote"))

    if mode == "current":
        return PushPlan(
            remote,
            (PushSpec(current, current),),
            mode,
            auto_setup_upstream=auto_setup and upstream is None,
        )

    if mode == "upstream":
        if upstream is None:
            if auto_setup:
                return PushPlan(
                    remote,
                    (PushSpec(current, current),),
                    mode,
                    auto_setup_upstream=True,
                )
            raise RuntimeError(f"current branch '{current}' has no upstream branch")
        if upstream.remote == "." or upstream.remote != remote:
            raise RuntimeError(
                f"push.default=upstream requires pushing to upstream remote "
                f"'{upstream.remote}', not '{remote}'"
            )
        return PushPlan(remote, (PushSpec(current, upstream.branch),), mode)

    # simple: on the branch's pull remote, require an upstream with the same
    # branch name. When pushing to a different repository, simple behaves like
    # current. With no tracking config, origin/the sole remote is the central
    # pull-side fallback and therefore requires an upstream unless
    # push.autoSetupRemote is enabled.
    if upstream is not None and upstream.remote == remote:
        if upstream.branch != current:
            raise RuntimeError(
                f"upstream branch '{upstream.branch}' does not match current "
                f"branch '{current}' for push.default=simple"
            )
        return PushPlan(remote, (PushSpec(current, current),), mode)

    if upstream is None and _default_fetch_remote(repo) == remote:
        if not auto_setup:
            raise RuntimeError(f"current branch '{current}' has no upstream branch")
        return PushPlan(
            remote,
            (PushSpec(current, current),),
            mode,
            auto_setup_upstream=True,
        )

    return PushPlan(
        remote,
        (PushSpec(current, current),),
        mode,
        auto_setup_upstream=auto_setup and upstream is None,
    )
