"""Git-compatible refspec-pattern validation for ``check-ref-format``.

Ordinary refnames reject ``*``.  Git's ``--refspec-pattern`` mode permits one
and only one wildcard while keeping every other refname rule intact.  This
module deliberately layers that exception over the mature ref validator rather
than duplicating its safety rules.
"""

from __future__ import annotations

from .ref_query import check_ref_format, normalize_refname


_WILDCARD_SENTINEL = "pygit-refspec-wildcard"


def check_refspec_pattern(
    refname: str,
    *,
    allow_onelevel: bool = False,
    normalize: bool = False,
) -> str:
    """Validate a Git refspec refname pattern and return the checked pattern.

    Exactly zero or one ``*`` is accepted.  A single wildcard is temporarily
    replaced with a normal refname-safe component fragment, allowing the
    existing validator to enforce all remaining Git refname rules.  The
    original wildcard is restored only after successful validation.
    """

    candidate = normalize_refname(refname) if normalize else refname
    wildcard_count = candidate.count("*")
    if wildcard_count > 1:
        raise ValueError("refspec pattern may contain at most one '*'")

    if wildcard_count == 0:
        return check_ref_format(candidate, allow_onelevel=allow_onelevel)

    checked = check_ref_format(
        candidate.replace("*", _WILDCARD_SENTINEL, 1),
        allow_onelevel=allow_onelevel,
    )
    return checked.replace(_WILDCARD_SENTINEL, "*", 1)
