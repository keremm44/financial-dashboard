from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase
from financial_dashboard.decision.execution import ExecutionTriggerState, assess_execution_trigger
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.structural import StructuralDirection


def _ref(as_of, *, quality=ContextDataQuality.VALID, available_at=None):
    timestamp = pd.Timestamp(as_of)
    available = timestamp if available_at is None else pd.Timestamp(available_at)
    return FactRef(
        ContextDomain.PATTERN,
        "PATTERN_BEHAVIOR",
        "TEST",
        "30m",
        f"PATTERN:{timestamp}",
        "STATE",
        timestamp,
        timestamp,
        available,
        None,
        CausalFamily.IMPULSE,
        SourceFamily.PRICE_GEOMETRY,
        quality,
    )


def _snapshot(
    as_of,
    *,
    phase,
    native_state,
    direction,
    quality=ContextDataQuality.VALID,
    available_at=None,
):
    timestamp = pd.Timestamp(as_of)
    row = SimpleNamespace(
        timeframe="30m",
        ref=_ref(timestamp, quality=quality, available_at=available_at),
        phase=phase,
        native_state=native_state,
        classic_direction=direction,
    )
    projection = SimpleNamespace(
        for_timeframe=lambda timeframe: row if timeframe == "30m" else (_ for _ in ()).throw(KeyError(timeframe))
    )
    return SimpleNamespace(as_of=timestamp, pattern_behavior=projection)


def test_detector_emits_only_fresh_transition_not_sticky_confirmation():
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 11:00")
    t3 = pd.Timestamp("2026-01-05 12:00")
    snapshots = (
        _snapshot(t1, phase=PatternBehaviorPhase.BREAK_CONFIRMING, native_state="BREAK_CANDIDATE", direction=1),
        _snapshot(t2, phase=PatternBehaviorPhase.BREAK_CONFIRMED, native_state="BREAK_CONFIRMED", direction=1),
        _snapshot(t3, phase=PatternBehaviorPhase.BREAK_CONFIRMED, native_state="BREAK_CONFIRMED", direction=1),
    )

    entry, exit_ = detect_30m_execution_events(snapshots)

    assert tuple(entry) == (t2,)
    assert exit_ == {}
    assert entry[t2].observed_at == t2
    assert entry[t2].side is StructuralDirection.LONG


def test_detector_routes_bearish_confirmation_to_long_exit_channel():
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 11:00")
    snapshots = (
        _snapshot(t1, phase=PatternBehaviorPhase.BREAK_CONFIRMING, native_state="BREAK_CANDIDATE", direction=-1),
        _snapshot(t2, phase=PatternBehaviorPhase.RETEST_HELD, native_state="RETEST_OK", direction=-1),
    )

    entry, exit_ = detect_30m_execution_events(snapshots)

    assert entry == {}
    assert tuple(exit_) == (t2,)
    assert exit_[t2].side is StructuralDirection.SHORT


def test_price_only_native_state_recovers_generic_data_limited_event_causally():
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 11:00")
    snapshots = (
        _snapshot(
            t1,
            phase=PatternBehaviorPhase.UNAVAILABLE,
            native_state="BREAK_CANDIDATE",
            direction=1,
            quality=ContextDataQuality.DATA_LIMITED,
        ),
        _snapshot(
            t2,
            phase=PatternBehaviorPhase.UNAVAILABLE,
            native_state="BREAK_CONFIRMED",
            direction=1,
            quality=ContextDataQuality.DATA_LIMITED,
        ),
    )

    entry, _ = detect_30m_execution_events(snapshots)
    event = entry[t2]

    assert event.source_refs[0].data_quality is ContextDataQuality.VALID
    assessment = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=t2,
        timeframe="30m",
        data_quality=ContextDataQuality.DATA_LIMITED,
        event=event,
    )
    assert assessment.state is ExecutionTriggerState.CONFIRMED


def test_future_unavailable_pattern_ref_never_creates_event():
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 11:00")
    snapshots = (
        _snapshot(t1, phase=PatternBehaviorPhase.BREAK_CONFIRMING, native_state="BREAK_CANDIDATE", direction=1),
        _snapshot(
            t2,
            phase=PatternBehaviorPhase.BREAK_CONFIRMED,
            native_state="BREAK_CONFIRMED",
            direction=1,
            available_at=t2 + pd.Timedelta(minutes=30),
        ),
    )

    entry, exit_ = detect_30m_execution_events(snapshots)
    assert entry == {}
    assert exit_ == {}
