from __future__ import annotations

from pygit.fetch_head import write_fetch_head
from pygit.fetch_policy import parse_fetch_refspec
from pygit.fetch_porcelain import fetch_porcelain
from pygit.remote import Advertisement
from pygit.repo import Repository


def _configured_repo(tmp_path):
    repo = Repository.init(str(tmp_path / "repo"))
    repo.add_remote("origin", "https://example.test/repo.git")
    repo.config_set("remote", "origin.url", "https://example.test/repo.git")
    repo.config_set(
        "remote",
        "origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )
    return repo


def test_parse_explicit_shorthand_destination():
    spec = parse_fetch_refspec("+maint:tmp")
    assert spec.source == "refs/heads/maint"
    assert spec.destination == "refs/heads/tmp"
    assert spec.force is True


def test_fetch_head_overwrite_and_append(tmp_path):
    pygit_dir = tmp_path / ".pygit"
    pygit_dir.mkdir()
    one = "a" * 64
    two = "b" * 64

    write_fetch_head(
        pygit_dir,
        {"refs/heads/main": one, "refs/heads/dev": two},
        source="https://example.test/repo.git",
        mergeable=["refs/heads/main"],
    )
    text = (pygit_dir / "FETCH_HEAD").read_text()
    assert f"{one}\t\tbranch 'main'" in text
    assert f"{two}\tnot-for-merge\tbranch 'dev'" in text

    write_fetch_head(
        pygit_dir,
        {"refs/tags/v1": one},
        source="https://example.test/repo.git",
        mergeable=["refs/tags/v1"],
        append=True,
    )
    text = (pygit_dir / "FETCH_HEAD").read_text()
    assert "branch 'main'" in text
    assert "tag 'v1'" in text


def test_explicit_source_uses_configured_refmap(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    internal = "a" * 64
    native = "b" * 40
    repo._write_native_map({internal: native}, "origin")

    class Client:
        def __init__(self, url):
            assert url == "https://example.test/repo.git"

        def discover(self):
            return Advertisement(
                {"refs/heads/dev": native},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("known object should not require upload-pack")

    monkeypatch.setattr("pygit.fetch_porcelain.SmartHttpClient", Client)
    result = fetch_porcelain(repo, "origin", refspecs=["dev"], tags=False)

    assert result["refs"] == {"refs/heads/dev": internal}
    assert repo.refs.get_remote("origin", "dev") == internal
    line = (repo.pygit_dir / "FETCH_HEAD").read_text()
    assert line.startswith(internal + "\t\t")
    assert "branch 'dev'" in line


def test_explicit_destination_overrides_configured_mapping(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    internal = "c" * 64
    native = "d" * 40
    repo._write_native_map({internal: native}, "origin")

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement({"refs/heads/dev": native}, set(), {})

        def fetch(self, *args, **kwargs):
            raise AssertionError("known object should not require upload-pack")

    monkeypatch.setattr("pygit.fetch_porcelain.SmartHttpClient", Client)
    fetch_porcelain(repo, "origin", refspecs=["dev:tmp"], tags=False)

    assert repo.refs.get_branch("tmp") == internal
    assert repo.refs.get_remote("origin", "dev") is None


def test_explicit_negative_filters_wildcard(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    dev_internal, main_internal = "1" * 64, "2" * 64
    dev_native, main_native = "3" * 40, "4" * 40
    repo._write_native_map(
        {dev_internal: dev_native, main_internal: main_native},
        "origin",
    )

    class Client:
        def __init__(self, url):
            pass

        def discover(self):
            return Advertisement(
                {"refs/heads/dev": dev_native, "refs/heads/main": main_native},
                set(),
                {},
            )

        def fetch(self, *args, **kwargs):
            raise AssertionError("known objects should not require upload-pack")

    monkeypatch.setattr("pygit.fetch_porcelain.SmartHttpClient", Client)
    result = fetch_porcelain(
        repo,
        "origin",
        refspecs=["refs/heads/*:refs/remotes/origin/*", "^refs/heads/dev"],
        tags=False,
    )

    assert result["refs"] == {"refs/heads/main": main_internal}
    assert repo.refs.get_remote("origin", "main") == main_internal
    assert repo.refs.get_remote("origin", "dev") is None


def test_ordinary_fetch_head_marks_remote_default_mergeable(tmp_path, monkeypatch):
    repo = _configured_repo(tmp_path)
    main, dev = "5" * 64, "6" * 64

    monkeypatch.setattr(
        "pygit.fetch_porcelain.fetch_configured",
        lambda *args, **kwargs: {
            "remote": "origin",
            "default_branch": "main",
            "refs": {"refs/heads/main": main, "refs/heads/dev": dev},
            "objects": 0,
            "pruned": [],
            "tag_mode": "auto",
        },
    )
    fetch_porcelain(repo, "origin")
    text = (repo.pygit_dir / "FETCH_HEAD").read_text()
    assert f"{main}\t\tbranch 'main'" in text
    assert f"{dev}\tnot-for-merge\tbranch 'dev'" in text
