from __future__ import annotations

import json

import pytest

from pygit.promisor import promised_size, read_promisor_state, update_promisor_state
from pygit.repo import Repository


def test_size_metadata_accepts_full_native_sha1_oid(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "A" * 40

    update_promisor_state(
        repo.pygit_dir,
        promised={native: "blob"},
        sizes={native: 7},
    )

    assert promised_size(repo.pygit_dir, native) == 7


@pytest.mark.parametrize(
    "bad_oid",
    [
        "a" * 39,
        "a" * 41,
        "a" * 64,
        "g" * 40,
        "native-object-id",
    ],
)
def test_size_metadata_rejects_non_native_object_ids(tmp_path, bad_oid):
    repo = Repository.init(str(tmp_path / "repo"))

    with pytest.raises(ValueError, match="full native SHA-1 object id"):
        update_promisor_state(
            repo.pygit_dir,
            promised={bad_oid: "blob"},
            sizes={bad_oid: 3},
        )

    assert read_promisor_state(repo.pygit_dir)["sizes"] == {}


def test_size_metadata_rejects_local_sha256_surrogate(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    native = "a" * 40
    local_sha256 = "b" * 64
    update_promisor_state(repo.pygit_dir, promised={native: "blob"})

    with pytest.raises(ValueError, match="full native SHA-1 object id"):
        update_promisor_state(repo.pygit_dir, sizes={local_sha256: 99})

    state = read_promisor_state(repo.pygit_dir)
    assert state["promised"] == {native: "blob"}
    assert state["sizes"] == {}


def test_persisted_invalid_size_oid_is_rejected_on_read(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    malformed = {
        "version": 1,
        "remotes": {"origin": {"filter": "blob:none"}},
        "promised": {"a" * 40: "blob"},
        "resolved": {},
        "sizes": {"b" * 64: 12},
    }
    (repo.pygit_dir / "promisor.json").write_text(
        json.dumps(malformed),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="full native SHA-1 object id"):
        read_promisor_state(repo.pygit_dir)


def test_invalid_size_oid_fails_before_persisting_promised_mutation(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    bad = "z" * 40

    with pytest.raises(ValueError, match="full native SHA-1 object id"):
        update_promisor_state(
            repo.pygit_dir,
            remote="origin",
            filter_spec="blob:none",
            promised={bad: "blob"},
            sizes={bad: 1},
        )

    assert not (repo.pygit_dir / "promisor.json").exists()
