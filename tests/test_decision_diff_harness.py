from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "decision_diff.py"


def _tool():
    spec = importlib.util.spec_from_file_location("migration_decision_diff", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _event(
    timestamp: str,
    action: str,
    *,
    horizon: str | None = "SHORT_TERM",
    blockers=(),
    waiting=(),
    execution_state: str | None = "ABSENT",
    position: str = "FLAT",
):
    return {
        "timestamp": timestamp,
        "action": action,
        "blockers": list(blockers),
        "waiting_for": list(waiting),
        "snapshot": {
            "entry_horizon": horizon,
            "execution": {
                "state": execution_state,
                "event_consumed": execution_state == "CONFIRMED",
            },
            "trade_lifecycle": {
                "position_state": position,
                "exit_stage": None,
            },
        },
    }


def test_identical_canonical_stream_has_empty_diff():
    tool = _tool()
    events = (
        _event("2026-01-01T10:00:00", "WAIT", waiting=("TIMING_DEVELOPING",)),
        _event("2026-01-01T10:30:00", "BUY", execution_state="CONFIRMED", position="OPEN"),
    )

    report = tool.compare_events(events, events)

    assert report.status == "UNCHANGED"
    assert report.is_empty
    assert report.bar_changes == ()
    assert report.action_timing_changes == ()


def test_moved_buy_is_classified_as_earlier_buy():
    tool = _tool()
    before = (
        _event("2026-01-01T10:00:00", "WAIT"),
        _event("2026-01-01T10:30:00", "BUY", execution_state="CONFIRMED", position="OPEN"),
    )
    after = (
        _event("2026-01-01T10:00:00", "BUY", execution_state="CONFIRMED", position="OPEN"),
        _event("2026-01-01T10:30:00", "HOLD", position="OPEN"),
    )

    report = tool.compare_events(before, after)

    assert not report.is_empty
    assert any(item.startswith("EARLIER BUY:") for item in report.action_timing_changes)
    assert report.classification_counts["EARLIER BUY"] == 1


def test_added_buy_is_reported_explicitly():
    tool = _tool()
    before = (_event("2026-01-01T10:00:00", "WAIT"),)
    after = (_event("2026-01-01T10:00:00", "BUY", execution_state="CONFIRMED", position="OPEN"),)

    report = tool.compare_events(before, after)

    assert report.classification_counts["ADDED BUY"] >= 1
    assert "ACTION CHANGED" in report.bar_changes[0].classifications


def test_non_action_fingerprint_changes_are_not_hidden():
    tool = _tool()
    before = (
        _event(
            "2026-01-01T10:00:00",
            "WAIT",
            blockers=("OLD_BLOCKER",),
            waiting=("OLD_WAIT",),
            execution_state="ABSENT",
            position="FLAT",
        ),
    )
    after = (
        _event(
            "2026-01-01T10:00:00",
            "WAIT",
            blockers=("NEW_BLOCKER",),
            waiting=("NEW_WAIT",),
            execution_state="UNAVAILABLE",
            position="OPEN",
        ),
    )

    report = tool.compare_events(before, after)
    change = report.bar_changes[0]

    assert change.changed_fields == ("blockers", "waiting_for", "execution", "lifecycle")
    assert set(change.classifications) == {
        "BLOCKERS CHANGED",
        "WAITING CHANGED",
        "EXECUTION CHANGED",
        "LIFECYCLE CHANGED",
    }
