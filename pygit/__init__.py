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

__all__ = ["Repository", "is_ancestor", "list_refs", "merge_bases"]
__version__ = "0.1.0"
