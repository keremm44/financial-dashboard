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


def test_causal_reducer_ingests_each_bar_once_and_freezes_at_cutoffs() -> None:
    runtime = _Runtime()
    reducer = CausalTimelineReducer(
        runtime=runtime,
        compose_decision=lambda state, cutoff: (state["rows"], pd.Timestamp(cutoff)),
        fingerprint=_fingerprint(),
    )
    events = (
        CausalBarEvent(
            available_at=pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"),
            timeframe="30m",
            bar_index=0,
            bar={"close": 1.0},
        ),
        CausalBarEvent(
            available_at=pd.Timestamp("2026-01-01 10:30", tz="Europe/Istanbul"),
            timeframe="30m",
            bar_index=1,
            bar={"close": 2.0},
        ),
        CausalBarEvent(
            available_at=pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul"),
            timeframe="1h",
            bar_index=0,
            bar={"close": 3.0},
        ),
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


def test_causal_reducer_rejects_watermark_gap() -> None:
    runtime = _Runtime()
    reducer = CausalTimelineReducer(
        runtime=runtime,
        compose_decision=lambda state, cutoff: state,
        fingerprint=_fingerprint(),
    )

    with pytest.raises(ValueError, match="non-contiguous 30m watermark"):
        reducer.run(
            events=(
                CausalBarEvent(
                    available_at=pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"),
                    timeframe="30m",
                    bar_index=1,
                    bar={"close": 1.0},
                ),
            ),
            cutoffs=(pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"),),
        )
