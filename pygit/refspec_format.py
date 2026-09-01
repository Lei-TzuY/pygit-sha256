"""Git-compatible refspec-pattern validation for ``check-ref-format``.

Ordinary refnames reject ``*``. Git's ``--refspec-pattern`` mode permits one
and only one wildcard while keeping every other refname rule intact. This
module deliberately layers that exception over the mature ref validator rather
than duplicating its safety rules.
"""

from __future__ import annotations

from .ref_query import check_ref_format, normalize_refname


def check_refspec_pattern(
    refname: str,
    *,
    allow_onelevel: bool = False,
    normalize: bool = False,
) -> str:
    """Validate a Git refspec refname pattern and return the checked pattern.

    Zero or one ``*`` is accepted. A single wildcard is temporarily replaced
    with a normal refname-safe character so the existing validator can enforce
    every other Git refname rule. The already-normalized original candidate is
    returned after successful validation, avoiding any sentinel-collision edge
    case in user-controlled ref text.
    """

    candidate = normalize_refname(refname) if normalize else refname
    wildcard_count = candidate.count("*")
    if wildcard_count > 1:
        raise ValueError("refspec pattern may contain at most one '*'")

    if wildcard_count == 0:
        check_ref_format(candidate, allow_onelevel=allow_onelevel)
        return candidate

    check_ref_format(
        candidate.replace("*", "x", 1),
        allow_onelevel=allow_onelevel,
    )
    return candidate
