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
from .cat_file import CatFileRecord, inspect_object, object_exists, resolve_object
from .checkout_index import checkout_index
from .hash_object import hash_object_data, hash_path, object_envelope, write_object_data
from .diff_plumbing import DiffEntry, diff_files, diff_index, diff_tree, format_diff_entries
from .fsck import FsckIssue, FsckReport, fsck
from .revision import (
    RevisionResult,
    abbreviate_oid,
    glob_refs,
    namespace_refs,
    resolve_abbreviation,
    resolve_many,
    resolve_revision,
    symbolic_refname,
)

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
    "CatFileRecord",
    "resolve_object",
    "inspect_object",
    "object_exists",
    "checkout_index",
    "hash_object_data",
    "write_object_data",
    "hash_path",
    "object_envelope",
    "DiffEntry",
    "diff_tree",
    "diff_index",
    "diff_files",
    "format_diff_entries",
    "FsckIssue",
    "FsckReport",
    "fsck",
    "RevisionResult",
    "resolve_revision",
    "resolve_abbreviation",
    "resolve_many",
    "symbolic_refname",
    "abbreviate_oid",
    "namespace_refs",
    "glob_refs",
]
__version__ = "0.1.0"
