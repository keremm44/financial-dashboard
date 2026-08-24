from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDomain
from financial_dashboard.context.pattern_behavior_projection import (
    PatternBehaviorPhase,
    project_pattern_behavior,
)
from financial_dashboard.engines.pattern_compression_core import (
    ST_BREAK_CANDIDATE,
    ST_RETESTING,
)
from financial_dashboard.engines.pattern_compression_engine import PatternExport


NOW = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)


def _available_at(timestamp, timeframe):
    assert timeframe == "1h"
    return timestamp + timedelta(minutes=1)


def _replay(*, native_state=ST_BREAK_CANDIDATE, quality="DATA_OK"):
    snapshot = SimpleNamespace(
        timeframe="1h",
        as_of=NOW,
        bar_count=40,
        native_state=native_state,
        active_start_bar=12,
        active_known_bar=18,
        progress=0.63,
        contraction=0.37,
        raw_quality=68.0,
        selection_score=72.0,
        upper_touches=3,
        lower_touches=2,
        quality_frozen=True,
        export=PatternExport(
            state=8,
            pattern_type=3,
            quality=70.0,
            classic_direction=0,
            break_state=2,
            break_level=108.0,
            break_strength=61.0,
            retest_state=0,
            retest_tolerance=0.45,
            identity=4.0,
        ),
    )
    input_batch = SimpleNamespace(source_quality=SimpleNamespace(status=quality))
    replay_row = SimpleNamespace(input_batch=input_batch)
    structure_location = SimpleNamespace(replay_for=lambda timeframe: replay_row)
    return SimpleNamespace(
        symbol="ASELS",
        timeframes=("1h",),
        pattern_snapshots=(snapshot,),
        structure_location=structure_location,
    )


def test_pattern_behavior_exposes_native_phase_age_progress_and_quality() -> None:
    projection = project_pattern_behavior(_replay(), available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("1h")

    assert row.ref.domain is ContextDomain.PATTERN
    assert row.ref.fact_type == "PATTERN_BEHAVIOR"
    assert row.ref.available_at == NOW + timedelta(minutes=1)
    assert row.phase is PatternBehaviorPhase.BREAK_CONFIRMING
    assert row.age_bars == 27
    assert row.bars_since_known == 21
    assert row.progress == 0.63
    assert row.contraction == 0.37
    assert row.raw_quality == 68.0
    assert row.selection_score == 72.0
    assert row.upper_touches == 3
    assert row.lower_touches == 2
    assert row.break_strength == 61.0
    assert row.quality_frozen is True


def test_retest_is_separate_from_break_confirmation() -> None:
    projection = project_pattern_behavior(
        _replay(native_state=ST_RETESTING),
        available_at=_available_at,
    )
    assert projection is not None
    assert projection.for_timeframe("1h").phase is PatternBehaviorPhase.POST_BREAK_RETEST


def test_limited_data_is_unavailable_not_no_pattern() -> None:
    projection = project_pattern_behavior(
        _replay(quality="DATA_LIMITED"),
        available_at=_available_at,
    )
    assert projection is not None
    assert projection.for_timeframe("1h").phase is PatternBehaviorPhase.UNAVAILABLE


def test_pattern_behavior_respects_knowledge_boundary() -> None:
    projection = project_pattern_behavior(_replay(), available_at=_available_at)
    assert projection is not None

    assert projection.available_at(NOW).timeframe_facts == ()
    assert len(projection.available_at(NOW + timedelta(minutes=1)).timeframe_facts) == 1
