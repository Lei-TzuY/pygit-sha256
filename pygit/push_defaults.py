"""Resolve Git-style default and explicit push refspecs."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from .remote_ops import configured_upstream
from .repo import Repository


@dataclass(frozen=True)
class PushSpec:
    """One local ref -> remote ref update."""

    source: str
    target: str
    force: bool = False
    namespace: str = "heads"
    delete: bool = False

    @property
    def source_ref(self) -> str:
        if self.delete:
            return ""
        return f"refs/{self.namespace}/{self.source}"

    @property
    def target_ref(self) -> str:
        return f"refs/{self.namespace}/{self.target}"


@dataclass(frozen=True)
class PushPlan:
    remote: str
    specs: Tuple[PushSpec, ...]
    mode: str
    auto_setup_upstream: bool = False


def _true(value: Optional[str]) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _current_branch(repo: Repository) -> str:
    branch = repo.refs.current_branch()
    if not branch:
        raise RuntimeError("cannot choose a branch push refspec from detached HEAD")
    return branch


def _ref_parts(value: str, *, field: str) -> Tuple[str, str]:
    if value.startswith("refs/heads/"):
        name = value[len("refs/heads/") :]
        namespace = "heads"
    elif value.startswith("refs/tags/"):
        name = value[len("refs/tags/") :]
        namespace = "tags"
    elif value.startswith("refs/"):
        raise RuntimeError(f"unsupported push {field} namespace: '{value}'")
    else:
        name = value
        namespace = "heads"
    if not name:
        raise RuntimeError(f"empty push {field} ref is not supported")
    return namespace, name


def _source(repo: Repository, value: str) -> Tuple[str, str]:
    if value == "HEAD":
        return "heads", _current_branch(repo)
    namespace, name = _ref_parts(value, field="source")
    oid = repo.refs.get_branch(name) if namespace == "heads" else repo.refs.get_tag(name)
    if not oid:
        kind = "branch" if namespace == "heads" else "tag"
        raise KeyError(f"Unknown local {kind}: '{name}'")
    return namespace, name


def _matching_specs(repo: Repository, remote: str, *, force: bool = False) -> Tuple[PushSpec, ...]:
    local = set(repo.refs.list_branches())
    remote_names = set(repo.refs.list_remotes(remote))
    return tuple(PushSpec(branch, branch, force=force) for branch in sorted(local & remote_names))


def _pattern_specs(repo: Repository, text: str, *, force: bool) -> Tuple[PushSpec, ...]:
    src, dst = text.split(":", 1)
    if src.count("*") != 1 or dst.count("*") != 1:
        raise RuntimeError(f"push wildcard refspec requires one '*' on each side: '{text}'")
    src_ns, src_pat = _ref_parts(src, field="source")
    dst_ns, dst_pat = _ref_parts(dst, field="destination")
    if src_ns != dst_ns:
        raise RuntimeError("wildcard push refspecs must stay within one ref namespace")

    prefix, suffix = src_pat.split("*", 1)
    dpre, dsuf = dst_pat.split("*", 1)
    names = repo.refs.list_branches() if src_ns == "heads" else repo.refs.list_tags()
    specs = []
    for name in names:
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        middle_end = len(name) - len(suffix) if suffix else len(name)
        middle = name[len(prefix) : middle_end]
        specs.append(PushSpec(name, f"{dpre}{middle}{dsuf}", force=force, namespace=src_ns))
    return tuple(specs)


def parse_push_refspec(repo: Repository, text: str) -> Tuple[PushSpec, ...]:
    """Parse positive branch/tag, deletion, matching, and pattern refspecs."""
    raw = text.strip()
    if not raw:
        raise RuntimeError("empty push refspec")
    force = raw.startswith("+")
    body = raw[1:] if force else raw
    if body == ":":
        return (PushSpec(":", ":", force=force),)
    if body.startswith("^"):
        raise RuntimeError("negative push refspec must be combined with a positive refspec")
    if body.count(":") > 1:
        raise RuntimeError(f"invalid push refspec: '{text}'")
    if "*" in body:
        if ":" not in body:
            raise RuntimeError("wildcard push refspec requires an explicit destination")
        return _pattern_specs(repo, body, force=force)

    if body.startswith(":"):
        target_text = body[1:]
        namespace, target = _ref_parts(target_text, field="destination")
        return (PushSpec("", target, force=force, namespace=namespace, delete=True),)
    if body.endswith(":"):
        raise RuntimeError(f"empty push destination is not supported: '{text}'")

    if ":" in body:
        source_text, target_text = body.split(":", 1)
        src_ns, source = _source(repo, source_text)
        dst_ns, target = _ref_parts(target_text, field="destination")
        if not target_text.startswith("refs/"):
            dst_ns = src_ns
        if src_ns != dst_ns:
            raise RuntimeError("cross-namespace push refspecs are not supported")
    else:
        src_ns, source = _source(repo, body)
        target = source
        dst_ns = src_ns
    return (PushSpec(source, target, force=force, namespace=dst_ns),)


def _negative_pattern(text: str) -> Tuple[str, str]:
    raw = text.strip()
    if not raw.startswith("^"):
        raise RuntimeError("not a negative push refspec")
    body = raw[1:]
    if not body or ":" in body or body.startswith("+"):
        raise RuntimeError("negative push refspecs must contain only a source ref")
    if len(body) in {40, 64} and all(ch in "0123456789abcdefABCDEF" for ch in body):
        raise RuntimeError("negative push refspecs cannot use raw object IDs")
    namespace, pattern = _ref_parts(body, field="source")
    if pattern.count("*") > 1:
        raise RuntimeError("negative push refspec supports at most one '*'")
    return namespace, pattern


def _matches_negative(spec: PushSpec, negative: Tuple[str, str]) -> bool:
    namespace, pattern = negative
    if spec.delete or spec.namespace != namespace:
        return False
    if "*" not in pattern:
        return spec.source == pattern
    prefix, suffix = pattern.split("*", 1)
    return spec.source.startswith(prefix) and spec.source.endswith(suffix)


def _expand_refspecs(repo: Repository, remote: str, values: Iterable[str]) -> Tuple[PushSpec, ...]:
    positives = []
    negatives = []
    for value in values:
        if value.strip().startswith("^"):
            negatives.append(_negative_pattern(value))
            continue
        parsed = parse_push_refspec(repo, value)
        if len(parsed) == 1 and parsed[0].source == parsed[0].target == ":":
            positives.extend(_matching_specs(repo, remote, force=parsed[0].force))
        else:
            positives.extend(parsed)
    if negatives and not positives:
        raise RuntimeError("negative push refspec requires at least one positive refspec")
    return tuple(
        spec for spec in positives
        if not any(_matches_negative(spec, negative) for negative in negatives)
    )


def all_branch_specs(repo: Repository, *, force: bool = False) -> Tuple[PushSpec, ...]:
    return tuple(PushSpec(name, name, force=force) for name in repo.refs.list_branches())


def all_tag_specs(repo: Repository, *, force: bool = False) -> Tuple[PushSpec, ...]:
    return tuple(PushSpec(name, name, force=force, namespace="tags") for name in repo.refs.list_tags())


def delete_specs(repo: Repository, values: Sequence[str]) -> Tuple[PushSpec, ...]:
    specs = []
    for value in values:
        raw = value[1:] if value.startswith("+") else value
        if ":" in raw or "*" in raw or raw.startswith("^"):
            raise RuntimeError("--delete accepts ref names, not refspec mappings")
        namespace, target = _ref_parts(raw, field="destination")
        specs.append(PushSpec("", target, namespace=namespace, delete=True))
    return tuple(specs)


def configured_push_refspecs(repo: Repository, remote: str) -> Tuple[str, ...]:
    value = repo.config_get("remote", f"{remote}.push")
    return tuple(shlex.split(value)) if value else ()


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


def resolve_push_plan(repo: Repository, remote: str, refspecs: Sequence[str] = ()) -> PushPlan:
    explicit = tuple(refspecs)
    if explicit:
        return PushPlan(remote, _expand_refspecs(repo, remote, explicit), "explicit")

    configured = configured_push_refspecs(repo, remote)
    if configured:
        return PushPlan(remote, _expand_refspecs(repo, remote, configured), "remote.push")

    current = _current_branch(repo)
    mode = push_default(repo)
    if mode == "nothing":
        raise RuntimeError('no refspec was specified and push.default is "nothing"')
    if mode == "matching":
        return PushPlan(remote, _matching_specs(repo, remote), mode)

    upstream = configured_upstream(repo, current)
    auto_setup = _true(repo.config_get("push", "autoSetupRemote"))
    if mode == "current":
        return PushPlan(remote, (PushSpec(current, current),), mode, auto_setup_upstream=auto_setup and upstream is None)
    if mode == "upstream":
        if upstream is None:
            if auto_setup:
                return PushPlan(remote, (PushSpec(current, current),), mode, auto_setup_upstream=True)
            raise RuntimeError(f"current branch '{current}' has no upstream branch")
        if upstream.remote == "." or upstream.remote != remote:
            raise RuntimeError(f"push.default=upstream requires pushing to upstream remote '{upstream.remote}', not '{remote}'")
        return PushPlan(remote, (PushSpec(current, upstream.branch),), mode)

    if upstream is not None and upstream.remote == remote:
        if upstream.branch != current:
            raise RuntimeError(f"upstream branch '{upstream.branch}' does not match current branch '{current}' for push.default=simple")
        return PushPlan(remote, (PushSpec(current, current),), mode)
    if upstream is None and _default_fetch_remote(repo) == remote:
        if not auto_setup:
            raise RuntimeError(f"current branch '{current}' has no upstream branch")
        return PushPlan(remote, (PushSpec(current, current),), mode, auto_setup_upstream=True)
    return PushPlan(remote, (PushSpec(current, current),), mode, auto_setup_upstream=auto_setup and upstream is None)
