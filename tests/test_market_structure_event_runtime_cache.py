from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.market_structure_event_runtime import (
    RuntimeMarketStructureEventLedger,
)
from financial_dashboard.engines.market_structure_events import MarketStructureEventLedger
from financial_dashboard.engines.market_structure_state import (
    EVENT_BOS,
    EVENT_CHOCH,
    EVENT_TRANSITION_FAIL,
    StructureEvent,
)


def _rows(count: int = 40) -> list[dict[str, object]]:
    timestamps = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
    return [
        {
            "timestamp": timestamp,
            "high": 110.0 + index,
            "low": 90.0 - index * 0.1,
            "close": 100.0 + index * 0.2,
        }
        for index, timestamp in enumerate(timestamps)
    ]


def _event(identity: int, event_type: str, direction: int, event_bar: int) -> StructureEvent:
    return StructureEvent(
        valid=True,
        identity=identity,
        scope="EXTERNAL",
        event_type=event_type,
        direction=direction,
        candidate_bar=event_bar - 1,
        event_bar=event_bar,
        broken_swing_identity=identity * 10,
        broken_source_bar=max(0, event_bar - 3),
        origin_swing_identity=identity * 10 + 1,
        origin_source_bar=max(0, event_bar - 2),
        level=100.0 + identity,
        origin_price=99.0 + identity,
        quality=70.0,
        evidence_text=event_type,
    )


def test_runtime_event_ledger_matches_canonical_across_bars_and_appends() -> None:
    rows = _rows()
    canonical = MarketStructureEventLedger()
    runtime = RuntimeMarketStructureEventLedger()
    events = (
        _event(1, EVENT_CHOCH, 1, 3),
        _event(2, EVENT_CHOCH, 1, 5),
        _event(3, EVENT_BOS, 1, 7),
        _event(4, EVENT_CHOCH, -1, 11),
        _event(5, EVENT_TRANSITION_FAIL, 1, 13),
    )

    for event in events:
        canonical.append(event, rows)
        runtime.append(event, rows)
        for current_bar in (event.event_bar, event.event_bar + 1, 30):
            assert runtime.snapshot(current_bar=current_bar) == canonical.snapshot(
                current_bar=current_bar
            )


def test_runtime_event_ledger_same_bar_snapshot_is_stable_and_reset_safe() -> None:
    rows = _rows()
    runtime = RuntimeMarketStructureEventLedger()
    runtime.extend(
        (
            _event(1, EVENT_CHOCH, 1, 3),
            _event(2, EVENT_BOS, 1, 7),
        ),
        rows,
    )

    first = runtime.snapshot(current_bar=20)
    second = runtime.snapshot(current_bar=20)
    assert first is second

    runtime.reset()
    assert runtime.snapshot(current_bar=20) == ()
