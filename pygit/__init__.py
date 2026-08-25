"""
pygit
=====
A minimal Git clone in Python.

Quick start::

    from pygit import Repository

    repo = Repository.init("/tmp/my-project")
    repo.add(["."])
    sha = repo.commit("Initial commit", author_name="Alice", author_email="alice@example.com")
    print(repo.log())
"""

from .repo import Repository
from .plumbing import is_ancestor, list_refs, merge_bases
from .graph_query import independent_commits, merge_bases_many, octopus_merge_bases
from .name_rev import NameRevResult, name_all, name_revision, name_revisions
from .packed_refs import PackedRef, pack_refs, read_packed_refs

__all__ = [
    "Repository",
    "is_ancestor",
    "list_refs",
    "merge_bases",
    "merge_bases_many",
    "octopus_merge_bases",
    "independent_commits",
    "NameRevResult",
    "name_revision",
    "name_revisions",
    "name_all",
    "PackedRef",
    "pack_refs",
    "read_packed_refs",
]
__version__ = "0.1.0"
