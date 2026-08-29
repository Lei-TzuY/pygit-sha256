from __future__ import annotations

import pytest

from pygit.promisor import read_promisor_state
from pygit.rev_list_disk_usage_cli import run_rev_list_disk_usage
from tests.test_phase242 import _disable_fetch, _partial_range_repo, _tree


@pytest.mark.parametrize("missing_mode", ["print", "print-info"])
def test_missing_objects_edge_boundary_deduplicates_explicit_edge(
    tmp_path, monkeypatch, capsys, missing_mode
):
    repo, base, tip, base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    before = read_promisor_state(repo.pygit_dir)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            f"--missing={missing_mode}",
            f"{base}..{tip}",
        ]
    ) == 0

    missing = f"?{tip_blob}"
    if missing_mode == "print-info":
        missing += " path=f.txt type=blob"
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"-{base}", tip, f"{_tree(repo, tip)} ", missing]
    assert lines.count(f"-{base}") == 1
    assert base_blob not in "\n".join(lines)
    assert _tree(repo, base) not in "\n".join(lines)
    assert read_promisor_state(repo.pygit_dir) == before


@pytest.mark.parametrize("missing_mode", ["print", "print-info"])
def test_missing_objects_edge_boundary_count_does_not_count_duplicate_edge(
    tmp_path, monkeypatch, capsys, missing_mode
):
    repo, base, tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    assert run_rev_list_disk_usage(
        [
            "--objects-edge",
            "--boundary",
            f"--missing={missing_mode}",
            "--count",
            f"{base}..{tip}",
        ]
    ) == 0

    missing = f"?{tip_blob}"
    if missing_mode == "print-info":
        missing += " path=f.txt type=blob"
    # Native Git advertises the excluded edge but does not include it in the
    # final object count. The missing promise is likewise reported but not
    # counted; only the selected commit and selected tree remain present.
    assert capsys.readouterr().out.splitlines() == [f"-{base}", missing, "2"]


def test_missing_objects_edge_boundary_no_overlap_keeps_boundary_stream(
    tmp_path, monkeypatch, capsys
):
    repo, _base, _tip, _base_blob, tip_blob = _partial_range_repo(tmp_path)
    monkeypatch.setattr("pygit.rev_list_promisor_cli._find_repo", lambda: repo)
    _disable_fetch(monkeypatch)
    capsys.readouterr()

    # HEAD has no explicit negative revision, hence no object edge. Boundary
    # mode is still accepted and the ordinary print-info traversal remains
    # authoritative.
    assert run_rev_list_disk_usage(
        ["--objects-edge", "--boundary", "--missing=print-info", "HEAD"]
    ) == 0

    lines = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("-") for line in lines)
    assert any(line == f"?{tip_blob} path=f.txt type=blob" for line in lines)
