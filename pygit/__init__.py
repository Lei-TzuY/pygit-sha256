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
from .merge_index_stages import install_repository_merge_stage_support
from .replay_index_stages import install_repository_replay_stage_support
from .shallow_native_export import install_native_export_shallow_support
from .promisor_store import install_promisor_store_support
from .promisor_checkout import install_promisor_checkout_support

install_repository_merge_stage_support(Repository)
install_repository_replay_stage_support(Repository)
install_native_export_shallow_support()
install_promisor_store_support()
install_promisor_checkout_support(Repository)

from .plumbing import is_ancestor, list_refs, merge_bases
from .graph_query import independent_commits, merge_bases_many, octopus_merge_bases
from .fork_point import fork_point
from .name_rev import NameRevResult, name_all, name_revision, name_revisions
from .packed_refs import PackedRef, pack_refs, read_packed_refs
from .cat_file import (
    CatFileBatchCommand,
    CatFileRecord,
    all_object_ids,
    batch_all_objects,
    batch_format_uses_rest,
    format_batch_object,
    format_batch_record,
    inspect_object,
    object_disk_size,
    object_exists,
    parse_batch_command,
    resolve_object,
    run_batch_commands,
    split_batch_input,
)
from .checkout_index import CheckoutTempRecord, checkout_index, checkout_index_temp
from .hash_object import hash_object_data, hash_path, object_envelope, write_object_data
from .diff_plumbing import DiffEntry, diff_files, diff_index, diff_tree, format_diff_entries
from .fsck import FsckIssue, FsckReport, fsck
from .gc import GarbageCollectResult, garbage_collect
from .ls_tree import LsTreeEntry, format_ls_tree, ls_tree
from .merge_file import MergeFileResult, merge_file, merge_file_data
from .merge_tree import MergeConflict, MergeTreeResult, merge_tree
from .mktag import make_tag, parse_tag_payload, validate_tag_payload
from .multi_pack_index import (
    MultiPackIndexEntry,
    ParsedMultiPackIndex,
    parse_multi_pack_index,
    parse_multi_pack_index_bytes,
    verify_multi_pack_index,
    write_multi_pack_index,
)
from .pack_index import PackIndexEntry, ParsedPackIndex, parse_index, parse_index_bytes
from .pack_objects import PackObjectsResult, pack_objects, reachable_objects, select_pack_objects
from .pack_plumbing import (
    IndexPackResult,
    PackEntry,
    ParsedPack,
    UnpackResult,
    build_index_bytes,
    index_pack,
    parse_pack,
    parse_pack_bytes,
    unpack_objects,
)
from .prune import PruneResult, default_expire_before, prune
from .prune_packed import PrunePackedResult, prune_packed
from .reflog_expire import (
    ReflogExpireEntry,
    ReflogExpireResult,
    default_reflog_expire_before,
    default_reflog_unreachable_before,
    expire_reflogs,
)
from .reflog_show import ReflogShowEntry, format_reflog_entry, normalize_reflog_ref, show_reflog
from .repack import RepackResult, repack
from .remote_query import LsRemoteResult, RemoteRef, ls_remote, resolve_remote_url
from .rev_list import RevListEntry, RevListObjectEntry, rev_list, rev_list_objects
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
from .show_ref import (
    ExcludeExistingResult,
    ShowRefEntry,
    exclude_existing_refs,
    format_show_refs,
    ref_exists,
    show_refs,
)

__all__ = [
    "Repository",
    "is_ancestor",
    "list_refs",
    "merge_bases",
    "merge_bases_many",
    "octopus_merge_bases",
    "independent_commits",
    "fork_point",
    "NameRevResult",
    "name_revision",
    "name_revisions",
    "name_all",
    "PackedRef",
    "pack_refs",
    "read_packed_refs",
    "CatFileRecord",
    "CatFileBatchCommand",
    "resolve_object",
    "inspect_object",
    "object_disk_size",
    "object_exists",
    "all_object_ids",
    "batch_all_objects",
    "format_batch_object",
    "format_batch_record",
    "batch_format_uses_rest",
    "split_batch_input",
    "parse_batch_command",
    "run_batch_commands",
    "CheckoutTempRecord",
    "checkout_index",
    "checkout_index_temp",
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
    "GarbageCollectResult",
    "garbage_collect",
    "LsTreeEntry",
    "ls_tree",
    "format_ls_tree",
    "MergeFileResult",
    "merge_file_data",
    "merge_file",
    "MergeConflict",
    "MergeTreeResult",
    "merge_tree",
    "parse_tag_payload",
    "validate_tag_payload",
    "make_tag",
    "MultiPackIndexEntry",
    "ParsedMultiPackIndex",
    "parse_multi_pack_index_bytes",
    "parse_multi_pack_index",
    "write_multi_pack_index",
    "verify_multi_pack_index",
    "PackIndexEntry",
    "ParsedPackIndex",
    "parse_index_bytes",
    "parse_index",
    "PackObjectsResult",
    "reachable_objects",
    "select_pack_objects",
    "pack_objects",
    "PackEntry",
    "ParsedPack",
    "IndexPackResult",
    "UnpackResult",
    "parse_pack_bytes",
    "parse_pack",
    "build_index_bytes",
    "index_pack",
    "unpack_objects",
    "PruneResult",
    "default_expire_before",
    "prune",
    "PrunePackedResult",
    "prune_packed",
    "ReflogExpireEntry",
    "ReflogExpireResult",
    "default_reflog_expire_before",
    "default_reflog_unreachable_before",
    "expire_reflogs",
    "ReflogShowEntry",
    "normalize_reflog_ref",
    "show_reflog",
    "format_reflog_entry",
    "RepackResult",
    "repack",
    "RemoteRef",
    "LsRemoteResult",
    "resolve_remote_url",
    "ls_remote",
    "RevListEntry",
    "RevListObjectEntry",
    "rev_list",
    "rev_list_objects",
    "RevisionResult",
    "resolve_revision",
    "resolve_abbreviation",
    "resolve_many",
    "symbolic_refname",
    "abbreviate_oid",
    "namespace_refs",
    "glob_refs",
    "ShowRefEntry",
    "show_refs",
    "ref_exists",
    "format_show_refs",
    "ExcludeExistingResult",
    "exclude_existing_refs",
]
__version__ = "0.1.0"