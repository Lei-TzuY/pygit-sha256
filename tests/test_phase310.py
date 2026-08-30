from __future__ import annotations

import json

import pytest

from pygit.promisor import read_promisor_state, update_promisor_state


def _write_state(pygit_dir, **overrides):
    state = {
        "version": 1,
        "remotes": {},
        "promised": {},
        "resolved": {},
        "sizes": {},
    }
    state.update(overrides)
    (pygit_dir / "promisor.json").write_text(json.dumps(state), encoding="utf-8")


def test_read_rejects_non_native_promised_oid(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    _write_state(pygit_dir, promised={"a" * 64: "blob"})

    with pytest.raises(ValueError, match="promised native object id"):
        read_promisor_state(pygit_dir)


def test_read_rejects_non_sha256_resolved_local_oid(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    _write_state(pygit_dir, resolved={"a" * 40: "b" * 40})

    with pytest.raises(ValueError, match="resolved local object id"):
        read_promisor_state(pygit_dir)


def test_read_rejects_malformed_identity_maps(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    _write_state(pygit_dir, promised=[])

    with pytest.raises(ValueError, match="promisor promised metadata must be an object"):
        read_promisor_state(pygit_dir)


def test_update_validates_before_touching_existing_state(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    native = "a" * 40
    update_promisor_state(pygit_dir, promised={native: "blob"})
    before = (pygit_dir / "promisor.json").read_bytes()

    with pytest.raises(ValueError, match="resolved local object id"):
        update_promisor_state(pygit_dir, resolved={native: "b" * 40})

    assert (pygit_dir / "promisor.json").read_bytes() == before


def test_valid_native_to_local_identity_boundary_round_trips(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    native = "A" * 40
    local = "B" * 64

    update_promisor_state(pygit_dir, promised={native: "blob"})
    update_promisor_state(pygit_dir, resolved={native: local})

    state = read_promisor_state(pygit_dir)
    assert state["resolved"] == {native: local}
    assert state["promised"] == {}
