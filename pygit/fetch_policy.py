"""Git-style fetch policy, refspec mapping, and prune configuration.

Phase183 keeps policy decisions separate from smart-HTTP transport. It resolves
CLI/config precedence for pruning and tag behavior and parses the fetch
refspecs used by named remotes, clone mappings, set-branches, and tag pruning.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import List, Optional

from .config import GitConfig
from .repo import Repository


def _parse_bool(value: Optional[str], *, key: str) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "on", "1"}:
        return True
    if normalized in {"false", "no", "off", "0"}:
        return False
    raise ValueError(f"invalid boolean value for {key}: {value!r}")


@dataclass(frozen=True)
class FetchPolicy:
    prune: bool
    prune_tags: bool
    tag_mode: str  # auto | all | none


@dataclass(frozen=True)
class FetchRefspec:
    raw: str
    source: str
    destination: Optional[str]
    force: bool = False
    negative: bool = False

    def matches_source(self, refname: str) -> bool:
        if "*" in self.source:
            return fnmatch.fnmatchcase(refname, self.source)
        return refname == self.source

    def destination_for(self, refname: str) -> Optional[str]:
        if self.destination is None or not self.matches_source(refname):
            return None
        if "*" not in self.source:
            return self.destination
        prefix, suffix = self.source.split("*", 1)
        middle = refname[len(prefix) :]
        if suffix:
            middle = middle[: -len(suffix)]
        return self.destination.replace("*", middle, 1)

    def source_for_destination(self, refname: str) -> Optional[str]:
        if self.destination is None:
            return None
        if "*" not in self.destination:
            return self.source if refname == self.destination else None
        prefix, suffix = self.destination.split("*", 1)
        if not refname.startswith(prefix) or (suffix and not refname.endswith(suffix)):
            return None
        middle = refname[len(prefix) :]
        if suffix:
            middle = middle[: -len(suffix)]
        return self.source.replace("*", middle, 1)


def parse_fetch_refspec(raw: str) -> FetchRefspec:
    token = raw.strip()
    if not token:
        raise ValueError("empty fetch refspec")

    negative = token.startswith("^")
    force = False
    if negative:
        token = token[1:]
    elif token.startswith("+"):
        force = True
        token = token[1:]

    if not token or token.count(":") > 1:
        raise ValueError(f"invalid fetch refspec: {raw!r}")

    if ":" in token:
        source, destination = token.split(":", 1)
        destination = destination or None
    else:
        source, destination = token, None

    if not source:
        raise ValueError(f"invalid fetch refspec: {raw!r}")
    if not source.startswith("refs/"):
        source = f"refs/heads/{source}"

    if negative and destination is not None:
        raise ValueError("negative fetch refspecs cannot specify a destination")
    if source.count("*") > 1:
        raise ValueError(f"unsupported fetch refspec pattern: {raw!r}")
    if destination is not None:
        if not destination.startswith("refs/"):
            raise ValueError(f"fetch destination must start with refs/: {destination!r}")
        if destination.count("*") > 1:
            raise ValueError(f"unsupported fetch destination pattern: {raw!r}")
        if ("*" in source) != ("*" in destination):
            raise ValueError("wildcard fetch refspecs require '*' on both sides")

    return FetchRefspec(raw, source, destination, force=force, negative=negative)


def configured_fetch_refspecs(repo: Repository, remote: str) -> List[FetchRefspec]:
    """Return configured mappings while preserving Phase182's empty-list state."""
    config = GitConfig(repo.pygit_dir)
    values = config.get_all("remote", f"{remote}.fetch")
    if values:
        return [parse_fetch_refspec(value) for value in values]
    # Phase182 made an absent fetch key meaningful once Git-style remote config
    # exists: `remote set-branches <name>` can intentionally clear all mappings.
    if config.get("remote", f"{remote}.url") is not None:
        return []
    # Historical JSON-only remotes keep pygit's all-heads compatibility default.
    return [parse_fetch_refspec(f"+refs/heads/*:refs/remotes/{remote}/*")]


def _config_bool(repo: Repository, remote: str, suffix: str, global_key: str) -> bool:
    remote_value = _parse_bool(
        repo.config_get("remote", f"{remote}.{suffix}"),
        key=f"remote.{remote}.{suffix}",
    )
    if remote_value is not None:
        return remote_value
    global_value = _parse_bool(repo.config_get("fetch", global_key), key=f"fetch.{global_key}")
    return bool(global_value) if global_value is not None else False


def _configured_tag_mode(repo: Repository, remote: str) -> str:
    value = repo.config_get("remote", f"{remote}.tagOpt")
    if value is None or not value.strip():
        return "auto"
    normalized = value.strip()
    if normalized == "--tags":
        return "all"
    if normalized == "--no-tags":
        return "none"
    raise ValueError(f"invalid remote.{remote}.tagOpt value: {value!r}")


def resolve_fetch_policy(
    repo: Repository,
    remote: str,
    *,
    prune: Optional[bool] = None,
    prune_tags: Optional[bool] = None,
    tags: Optional[bool] = None,
) -> FetchPolicy:
    resolved_prune = prune if prune is not None else _config_bool(repo, remote, "prune", "prune")
    resolved_prune_tags = (
        prune_tags
        if prune_tags is not None
        else _config_bool(repo, remote, "pruneTags", "pruneTags")
    )
    tag_mode = (
        "all" if tags is True else "none" if tags is False else _configured_tag_mode(repo, remote)
    )
    return FetchPolicy(
        prune=bool(resolved_prune),
        prune_tags=bool(resolved_prune_tags),
        tag_mode=tag_mode,
    )


def source_is_excluded(refname: str, specs: List[FetchRefspec]) -> bool:
    return any(spec.negative and spec.matches_source(refname) for spec in specs)
