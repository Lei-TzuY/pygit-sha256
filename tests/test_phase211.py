from __future__ import annotations

import pytest

from pygit import fetch_update_shallow_composition as composition


@pytest.mark.parametrize(
    "option",
    [
        "--depth=2",
        "--deepen=2",
        "--unshallow",
        "--shallow-since=2026-01-01",
        "--shallow-exclude=refs/tags/v1",
    ],
)
def test_update_shallow_combines_with_explicit_shallow_mutations(monkeypatch, option):
    calls = []
    monkeypatch.setattr(
        composition,
        "_run_shallow_fetch",
        lambda argv: calls.append(("shallow", list(argv))) or 0,
    )
    monkeypatch.setattr(
        composition,
        "_run_fetch",
        lambda argv: calls.append(("phase210", list(argv))) or 0,
    )

    assert composition.run_fetch(["--update-shallow", option, "origin"]) == 0
    assert calls == [("shallow", [option, "origin"])]


def test_update_shallow_standalone_keeps_phase210_safety(monkeypatch):
    calls = []
    monkeypatch.setattr(
        composition,
        "_run_shallow_fetch",
        lambda argv: calls.append(("shallow", list(argv))) or 0,
    )
    monkeypatch.setattr(
        composition,
        "_run_fetch",
        lambda argv: calls.append(("phase210", list(argv))) or 0,
    )

    assert composition.run_fetch(["--update-shallow", "origin"]) == 0
    assert calls == [("phase210", ["--update-shallow", "origin"])]


def test_ordinary_fetch_keeps_phase210_warning_only_guard(monkeypatch):
    calls = []
    monkeypatch.setattr(
        composition,
        "_run_fetch",
        lambda argv: calls.append(list(argv)) or 0,
    )

    assert composition.run_fetch(["origin"]) == 0
    assert calls == [["origin"]]


def test_update_shallow_respects_option_terminator(monkeypatch):
    calls = []
    monkeypatch.setattr(
        composition,
        "_run_shallow_fetch",
        lambda argv: calls.append(("shallow", list(argv))) or 0,
    )
    monkeypatch.setattr(
        composition,
        "_run_fetch",
        lambda argv: calls.append(("phase210", list(argv))) or 0,
    )

    assert composition.run_fetch(
        ["--update-shallow", "origin", "--", "--deepen=2"]
    ) == 0
    assert calls == [
        ("phase210", ["--update-shallow", "origin", "--", "--deepen=2"])
    ]


def test_server_option_payload_is_not_reparsed_as_shallow_option(monkeypatch):
    calls = []
    monkeypatch.setattr(
        composition,
        "_run_shallow_fetch",
        lambda argv: calls.append(("shallow", list(argv))) or 0,
    )
    monkeypatch.setattr(
        composition,
        "_run_fetch",
        lambda argv: calls.append(("phase210", list(argv))) or 0,
    )

    argv = ["--update-shallow", "-o", "--deepen=2", "origin"]
    assert composition.run_fetch(argv) == 0
    assert calls == [("phase210", argv)]


def test_server_option_payload_does_not_hide_real_shallow_option(monkeypatch):
    calls = []
    monkeypatch.setattr(
        composition,
        "_run_shallow_fetch",
        lambda argv: calls.append(list(argv)) or 0,
    )

    assert composition.run_fetch(
        ["--update-shallow", "-o", "trace=1", "--deepen=2", "origin"]
    ) == 0
    assert calls == [["-o", "trace=1", "--deepen=2", "origin"]]
