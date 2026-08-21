from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from financial_dashboard.engines.market_structure import (
    MarketStructureConfig,
    MarketStructureEngine as SwingCoreMarketStructureEngine,
)
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine
from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventLedger,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_state import (
    EVENT_BOS,
    EVENT_CHOCH,
    EVENT_TRANSITION_FAIL,
    StructureEvent,
)


def _rows(count: int = 10) -> list[dict[str, object]]:
    timestamps = pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC")
    return [{"timestamp": timestamp} for timestamp in timestamps]


def _event(
    *,
    identity: int,
    event_type: str,
    direction: int,
    event_bar: int,
    scope: str = "EXTERNAL",
) -> StructureEvent:
    return StructureEvent(
        valid=True,
        identity=identity,
        scope=scope,
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


def test_choch_history_links_local_bos_follow_through_without_mutating_old_snapshot() -> None:
    rows = _rows()
    ledger = MarketStructureEventLedger()
    ledger.append(
        _event(identity=1, event_type=EVENT_CHOCH, direction=1, event_bar=3),
        rows,
    )
    pending_snapshot = ledger.snapshot(current_bar=3)

    ledger.append(
        _event(identity=2, event_type=EVENT_BOS, direction=1, event_bar=5),
        rows,
    )
    current_snapshot = ledger.snapshot(current_bar=7)

    assert len(current_snapshot) == 2
    assert pending_snapshot[0].outcome is StructureEventOutcome.PENDING
    assert pending_snapshot[0].confirmed_by_event_uid is None
    assert current_snapshot[0].outcome is StructureEventOutcome.FOLLOW_THROUGH_CONFIRMED
    assert current_snapshot[0].relevance is StructureEventRelevance.HISTORICAL
    assert current_snapshot[0].confirmed_by_event_uid == current_snapshot[1].event_uid
    assert current_snapshot[0].age_bars == 4
    with pytest.raises(FrozenInstanceError):
        current_snapshot[0].age_bars = 99  # type: ignore[misc]


def test_transition_failure_retains_and_marks_failed_choch_in_same_scope() -> None:
    rows = _rows()
    ledger = MarketStructureEventLedger()
    ledger.extend(
        (
            _event(
                identity=1,
                event_type=EVENT_CHOCH,
                direction=-1,
                event_bar=3,
                scope="INTERNAL",
            ),
            _event(
                identity=2,
                event_type=EVENT_TRANSITION_FAIL,
                direction=1,
                event_bar=6,
                scope="INTERNAL",
            ),
        ),
        rows,
    )

    history = ledger.snapshot(current_bar=8)

    assert len(history) == 2
    assert history[0].validity is StructureEventValidity.FAILED
    assert history[0].outcome is StructureEventOutcome.FAILED
    assert history[0].failed_by_event_uid == history[1].event_uid
    assert history[1].scope == "INTERNAL"


def test_integrated_export_retains_typed_history_and_immutable_scope_snapshots() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=12, freq="h", tz="UTC"),
            "open": [9.5, 10.5, 12.0, 12.0, 10.5, 10.0, 11.5, 11.0, 13.0, 12.0, 8.5, 10.0],
            "high": [10, 11, 15, 12, 11, 12, 13, 12, 16, 13, 12, 13],
            "low": [9, 10, 11, 10, 7, 9, 10, 9, 11, 8, 7, 9],
            "close": [9.8, 10.8, 14.2, 11.3, 8.0, 11.2, 12.4, 10.0, 15.2, 9.0, 7.8, 12.0],
            "volume": [100.0] * 12,
            "is_closed": [True] * 12,
            "is_complete": [True] * 12,
        }
    )
    engine = MarketStructureEngine(
        MarketStructureConfig(
            external_pivot_len=2,
            internal_pivot_len=1,
            atr_length=3,
            external_min_atr_distance=0.10,
            internal_min_atr_distance=0.10,
            min_tick=0.01,
        )
    )

    engine.replay(frame)
    export = engine.export_contract

    assert export is not None
    assert export.contract_version == 2
    assert export.events == engine.event_history
    assert export.external_scope is not None
    assert export.internal_scope is not None
    assert export.external_scope.scope == "EXTERNAL"
    assert export.internal_scope.scope == "INTERNAL"
    assert all(event.candidate_bar is not None for event in export.events)
    assert all(event.confirmed_at is not None for event in export.events)


@pytest.mark.parametrize(
    "engine_type",
    (SwingCoreMarketStructureEngine, MarketStructureEngine),
)
def test_closed_but_incomplete_bar_cannot_advance_shared_or_integrated_state(engine_type) -> None:
    engine = engine_type(
        MarketStructureConfig(external_pivot_len=2, internal_pivot_len=1, atr_length=2)
    )
    closed = {
        "timestamp": pd.Timestamp("2026-01-01T10:00:00Z"),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "is_closed": True,
        "is_complete": True,
    }
    incomplete = {
        **closed,
        "timestamp": pd.Timestamp("2026-01-01T11:00:00Z"),
        "high": 20.0,
        "is_complete": False,
    }

    before = engine.update(closed)
    history_before = getattr(engine, "event_history", None)
    after = engine.update(incomplete)

    assert after is before
    if history_before is not None:
        assert engine.event_history == history_before
