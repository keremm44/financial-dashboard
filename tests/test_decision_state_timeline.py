from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.decision.causal_reducer import CausalBarEvent, CausalTimelineReducer
from financial_dashboard.decision.state_timeline import (
    AppendOnlyTimeline,
    TimelineFingerprint,
)


def _fingerprint() -> TimelineFingerprint:
    return TimelineFingerprint(
        symbol="TEST",
        engine_config="unit",
        clock_version="causal-v1",
    )


def _event(timeframe: str, index: int, timestamp: str, close: float) -> CausalBarEvent:
    return CausalBarEvent(
        available_at=pd.Timestamp(timestamp, tz="Europe/Istanbul"),
        timeframe=timeframe,
        bar_index=index,
        bar={"close": close},
    )


def test_append_only_timeline_rejects_non_increasing_as_of() -> None:
    timeline = AppendOnlyTimeline[str](fingerprint=_fingerprint())
    timeline.append("a", as_of=pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"))

    with pytest.raises(ValueError, match="strictly increasing"):
        timeline.append("b", as_of=pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"))


class _Runtime:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int]] = []

    def ingest(self, event: CausalBarEvent) -> None:
        self.rows.append((event.timeframe, event.bar_index))

    def freeze(self, *, as_of, watermarks):
        return {
            "as_of": pd.Timestamp(as_of),
            "rows": tuple(self.rows),
            "watermarks": dict(watermarks),
        }


def _reducer(runtime: _Runtime) -> CausalTimelineReducer:
    return CausalTimelineReducer(
        runtime=runtime,
        compose_decision=lambda state, cutoff: (state["rows"], pd.Timestamp(cutoff)),
        fingerprint=_fingerprint(),
    )


def test_causal_reducer_ingests_each_bar_once_and_freezes_at_cutoffs() -> None:
    runtime = _Runtime()
    reducer = _reducer(runtime)
    events = (
        _event("30m", 0, "2026-01-01 10:00", 1.0),
        _event("30m", 1, "2026-01-01 10:30", 2.0),
        _event("1h", 0, "2026-01-01 11:00", 3.0),
    )

    store = reducer.run(
        events=events,
        cutoffs=(
            pd.Timestamp("2026-01-01 10:30", tz="Europe/Istanbul"),
            pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul"),
        ),
    )

    assert runtime.rows == [("30m", 0), ("30m", 1), ("1h", 0)]
    assert len(store.domains) == 2
    assert store.domains[0].watermarks == {"30m": 1}
    assert store.domains[1].watermarks == {"30m": 1, "1h": 0}
    assert store.decisions[0].domain_position == 0
    assert store.decisions[1].domain_position == 1


def test_same_reducer_continues_with_new_live_bars_without_replaying_history() -> None:
    runtime = _Runtime()
    reducer = CausalTimelineReducer(
        runtime=runtime,
        compose_decision=lambda state, cutoff: state,
        fingerprint=_fingerprint(),
    )

    cold = reducer.run(
        events=(
            _event("30m", 0, "2026-01-01 10:00", 1.0),
            _event("30m", 1, "2026-01-01 10:30", 2.0),
        ),
        cutoffs=(pd.Timestamp("2026-01-01 10:30", tz="Europe/Istanbul"),),
    )
    live = reducer.run(
        events=(_event("30m", 2, "2026-01-01 11:00", 3.0),),
        cutoffs=(pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul"),),
    )

    assert cold.domains[0].watermarks == {"30m": 1}
    assert live.domains[0].watermarks == {"30m": 2}
    assert runtime.rows == [("30m", 0), ("30m", 1), ("30m", 2)]


def test_future_extension_does_not_change_prior_frozen_state_or_decision() -> None:
    prefix_events = (
        _event("30m", 0, "2026-01-01 10:00", 1.0),
        _event("30m", 1, "2026-01-01 10:30", 2.0),
        _event("1h", 0, "2026-01-01 11:00", 3.0),
    )
    prefix_cutoff = pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul")

    prefix_store = _reducer(_Runtime()).run(
        events=prefix_events,
        cutoffs=(prefix_cutoff,),
    )
    extended_store = _reducer(_Runtime()).run(
        events=(
            *prefix_events,
            _event("30m", 2, "2026-01-01 11:30", 4.0),
            _event("1h", 1, "2026-01-01 12:00", 5.0),
        ),
        cutoffs=(
            prefix_cutoff,
            pd.Timestamp("2026-01-01 12:00", tz="Europe/Istanbul"),
        ),
    )

    assert extended_store.domains[0].as_of == prefix_store.domains[0].as_of
    assert extended_store.domains[0].state == prefix_store.domains[0].state
    assert extended_store.domains[0].watermarks == prefix_store.domains[0].watermarks
    assert extended_store.decisions[0].as_of == prefix_store.decisions[0].as_of
    assert extended_store.decisions[0].state == prefix_store.decisions[0].state


def test_causal_reducer_rejects_watermark_gap() -> None:
    runtime = _Runtime()
    reducer = CausalTimelineReducer(
        runtime=runtime,
        compose_decision=lambda state, cutoff: state,
        fingerprint=_fingerprint(),
    )

    with pytest.raises(ValueError, match="non-contiguous 30m watermark"):
        reducer.run(
            events=(_event("30m", 1, "2026-01-01 10:00", 1.0),),
            cutoffs=(pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"),),
        )
