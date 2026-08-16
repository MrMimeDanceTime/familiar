"""The coverage harness has to keep working, or the guard is imaginary.

`tools/coverage_report.py` is the regression test for scoring changes, and a
tool that silently stops running is worse than no tool — it reads as a passing
check. These tests cover the machinery (baseline round-trip, delta rendering,
regression detection), not the measurement itself, which needs the real card
index and real decks.

The report also broke on its own header the first time it ran with deltas:
Windows consoles default to cp1252 and cannot encode the symbols it prints.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import coverage_report  # noqa: E402


def _row(deck, mechanical, pool=80, scored=70):
    return {
        "deck": deck, "commander": "Cmd", "pool": pool, "scored": scored,
        "consensus": 40, "mechanical": mechanical, "personal": 30,
    }


@pytest.fixture
def baseline_path(tmp_path, monkeypatch):
    path = tmp_path / "baseline.json"
    monkeypatch.setattr(coverage_report, "BASELINE_PATH", path)
    return path


def test_baseline_round_trips(baseline_path):
    coverage_report.save_baseline([_row("Deck A", 30), _row("Deck B", 50)])
    loaded = coverage_report.load_baseline()

    assert loaded["decks"]["Deck A"]["mechanical"] == 30
    assert loaded["decks"]["Deck B"]["mechanical"] == 50
    assert "saved_at" in loaded


def test_missing_baseline_is_not_an_error(baseline_path):
    """First run on a fresh clone must report, not crash."""
    assert coverage_report.load_baseline() == {}


def test_corrupt_baseline_degrades_to_empty(baseline_path):
    baseline_path.write_text("{ this is not json", encoding="utf-8")
    assert coverage_report.load_baseline() == {}


def test_delta_marks_new_decks(baseline_path):
    assert "new" in coverage_report._delta(30, None)


def test_delta_shows_no_change_distinctly(baseline_path):
    """A deck that did not move should be visually quiet, so the eye lands on
    the ones that did."""
    assert coverage_report._delta(30, 30).strip() == "·"


def test_delta_signs_movement(baseline_path):
    assert coverage_report._delta(40, 30).strip() == "+10"
    assert coverage_report._delta(20, 30).strip() == "-10"


def test_report_exits_non_zero_on_a_drop(baseline_path, monkeypatch, capsys):
    """The exit code is what any automation keys on."""
    coverage_report.save_baseline([_row("Deck A", 50)])
    monkeypatch.setattr(coverage_report, "measure", lambda s, d: _row("Deck A", 20))
    monkeypatch.setattr(sys, "argv", ["coverage_report.py"])
    monkeypatch.setattr(
        coverage_report.repo, "list_decks",
        lambda session: [type("D", (), {"id": 1, "commander": "Cmd"})()],
    )

    exit_code = coverage_report.main()

    assert exit_code == 1
    assert "DROPPED on: Deck A" in capsys.readouterr().out


def test_report_exits_zero_when_coverage_holds(baseline_path, monkeypatch, capsys):
    coverage_report.save_baseline([_row("Deck A", 50)])
    monkeypatch.setattr(coverage_report, "measure", lambda s, d: _row("Deck A", 55))
    monkeypatch.setattr(sys, "argv", ["coverage_report.py"])
    monkeypatch.setattr(
        coverage_report.repo, "list_decks",
        lambda session: [type("D", (), {"id": 1, "commander": "Cmd"})()],
    )

    assert coverage_report.main() == 0
    assert "No mechanical-coverage regressions" in capsys.readouterr().out


def test_save_accepts_a_drop_deliberately(baseline_path, monkeypatch, capsys):
    """`--save` is the explicit "yes, I meant that" — so it must not also fail
    the run, or an intended change could never be recorded."""
    coverage_report.save_baseline([_row("Deck A", 50)])
    monkeypatch.setattr(coverage_report, "measure", lambda s, d: _row("Deck A", 20))
    monkeypatch.setattr(sys, "argv", ["coverage_report.py", "--save"])
    monkeypatch.setattr(
        coverage_report.repo, "list_decks",
        lambda session: [type("D", (), {"id": 1, "commander": "Cmd"})()],
    )

    assert coverage_report.main() == 0
    assert coverage_report.load_baseline()["decks"]["Deck A"]["mechanical"] == 20


def test_tool_runs_standalone():
    """It is invoked as a script, so an import-time break must be caught here
    rather than the next time someone changes scoring."""
    result = subprocess.run(
        [sys.executable, str(TOOLS / "coverage_report.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert "coverage" in result.stdout.lower()


def test_committed_baseline_is_valid():
    """The real baseline travels with the repo; a malformed one silently
    disables every future comparison."""
    path = TOOLS / "coverage_baseline.json"
    if not path.exists():
        pytest.skip("no baseline recorded yet")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["decks"]
    for name, row in data["decks"].items():
        assert isinstance(row["mechanical"], int), name
        assert isinstance(row["pool"], int), name
