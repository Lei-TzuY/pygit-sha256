from __future__ import annotations

from pygit import promisor_size_refresh as refresh
from pygit.promisor import promised_size, update_promisor_state
from pygit.repo import Repository


def _repo_with_promises(tmp_path, *native_oids: str):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.invalid/origin.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="origin",
        filter_spec="blob:none",
        promised={oid: "blob" for oid in native_oids},
    )
    return repo


def test_refresh_chunks_large_pending_set_deterministically(tmp_path, monkeypatch):
    native_oids = tuple(f"{value:040x}" for value in range(1, 6))
    repo = _repo_with_promises(tmp_path, *native_oids)
    queries = []

    monkeypatch.setattr(refresh, "OBJECT_INFO_SIZE_BATCH", 2)

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            assert url == "https://example.invalid/origin.git"

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            return {oid: int(oid, 16) for oid in oids}

    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    sizes = refresh.refresh_promisor_sizes(repo, tuple(reversed(native_oids)))

    assert queries == [
        native_oids[0:2],
        native_oids[2:4],
        native_oids[4:5],
    ]
    assert sizes == {oid: int(oid, 16) for oid in reversed(native_oids)}
    assert all(len(oid) == 40 for batch in queries for oid in batch)


def test_refresh_skips_persisted_sizes_before_chunking(tmp_path, monkeypatch):
    native_oids = tuple(f"{value:040x}" for value in range(10, 15))
    repo = _repo_with_promises(tmp_path, *native_oids)
    update_promisor_state(
        repo.pygit_dir,
        sizes={native_oids[1]: 101, native_oids[3]: 103},
    )
    queries = []

    monkeypatch.setattr(refresh, "OBJECT_INFO_SIZE_BATCH", 2)

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

        def query_sizes(self, oids):
            queries.append(tuple(oids))
            return {oid: 200 + index for index, oid in enumerate(oids)}

    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    refresh.refresh_promisor_sizes(repo, native_oids)

    assert queries == [
        (native_oids[0], native_oids[2]),
        (native_oids[4],),
    ]
    assert promised_size(repo.pygit_dir, native_oids[1]) == 101
    assert promised_size(repo.pygit_dir, native_oids[3]) == 103


def test_chunk_failure_stops_remote_and_falls_back_with_remaining_set(tmp_path, monkeypatch):
    native_oids = tuple(f"{value:040x}" for value in range(20, 25))
    repo = _repo_with_promises(tmp_path, *native_oids)
    repo.add_remote("zbackup", "https://example.invalid/backup.git")
    update_promisor_state(
        repo.pygit_dir,
        remote="zbackup",
        filter_spec="blob:none",
    )
    calls = []

    monkeypatch.setattr(refresh, "OBJECT_INFO_SIZE_BATCH", 2)

    class FakeObjectInfoClient:
        def __init__(self, url, *, server_options=()):
            self.url = url

        def query_sizes(self, oids):
            calls.append((self.url, tuple(oids)))
            if self.url.endswith("backup.git"):
                return {oid: 300 + int(oid, 16) for oid in oids}
            raise RuntimeError("origin object-info unavailable")

    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    result = refresh.refresh_promisor_sizes(repo, native_oids)

    assert calls == [
        ("https://example.invalid/origin.git", native_oids[0:2]),
        ("https://example.invalid/backup.git", native_oids[0:2]),
        ("https://example.invalid/backup.git", native_oids[2:4]),
        ("https://example.invalid/backup.git", native_oids[4:5]),
    ]
    assert set(result) == set(native_oids)


def test_chunk_size_guard_rejects_non_positive_configuration(tmp_path, monkeypatch):
    native = "f" * 40
    repo = _repo_with_promises(tmp_path, native)
    monkeypatch.setattr(refresh, "OBJECT_INFO_SIZE_BATCH", 0)

    class FakeObjectInfoClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(refresh, "SmartHttpV2ObjectInfoClient", FakeObjectInfoClient)

    try:
        refresh.refresh_promisor_sizes(repo, (native,))
    except ValueError as exc:
        assert "batch must be positive" in str(exc)
    else:
        raise AssertionError("expected invalid batch configuration to fail")
