from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from financial_dashboard.context.participation_behavior_projection import (
    AbsorptionBehavior,
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationTrend,
    ShockBehavior,
    project_participation_behavior,
)
from financial_dashboard.context.envelope import ContextDomain


NOW = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)


def _available_at(timestamp, timeframe):
    assert timeframe == "4h"
    return timestamp + timedelta(minutes=1)


def _replay(
    *,
    status="READY",
    state="PARTICIPATION_UP_CONFIRMED",
    data_quality="READY",
    **export_values,
):
    export = SimpleNamespace(
        participation_direction=export_values.get("participation_direction", 1),
        participation_stage=export_values.get("participation_stage", "CONFIRMED"),
        controlled_pullback=export_values.get("controlled_pullback", False),
        controlled_reaction=export_values.get("controlled_reaction", False),
        absorption_side=export_values.get("absorption_side", "NONE"),
        absorption_stage=export_values.get("absorption_stage", "NONE"),
        break_direction=export_values.get("break_direction", 0),
        break_stage=export_values.get("break_stage", "NONE"),
        heavy_conflict=export_values.get("heavy_conflict", False),
        one_bar_shock=export_values.get("one_bar_shock", False),
        shock_direction=export_values.get("shock_direction", 0),
        rvol=1.6,
        relative_traded_value=1.5,
        directional_value_pressure_5=0.28,
        directional_value_pressure_10=0.22,
        net_progress_atr=0.75,
        directional_efficiency=0.68,
        effort_result_class=export_values.get(
            "effort_result_class", "HIGH_EFFORT_STRONG_RESULT"
        ),
    )
    latest = SimpleNamespace(
        status=status,
        state=state,
        data_quality=data_quality,
        evidence_direction=1,
        timestamp=NOW,
        audit_export=export,
    )
    timeframe_replay = SimpleNamespace(timeframe="4h", latest=latest)
    return SimpleNamespace(
        symbol="ASELS",
        timeframes=("4h",),
        timeframe_replays=(timeframe_replay,),
    )


def test_projection_keeps_volume_dimensions_separate() -> None:
    replay = _replay(
        participation_stage="PROTECTED",
        effort_result_class="VERY_HIGH_EFFORT_WEAK_RESULT",
        absorption_side="UPPER",
        absorption_stage="CONFIRMED",
        break_direction=1,
        break_stage="SUPPORTED",
        controlled_pullback=True,
        heavy_conflict=True,
        one_bar_shock=True,
        shock_direction=1,
    )

    projection = project_participation_behavior(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.ref.domain is ContextDomain.VOLUME
    assert row.ref.fact_type == "PARTICIPATION_BEHAVIOR"
    assert row.ref.available_at == NOW + timedelta(minutes=1)
    assert row.participation_trend is ParticipationTrend.PROTECTED
    assert row.effort_result is EffortResultBehavior.WEAK_RESULT
    assert row.absorption is AbsorptionBehavior.CONFIRMED
    assert row.break_participation is BreakParticipationBehavior.SUPPORTED
    assert row.shock is ShockBehavior.ONE_BAR
    assert row.controlled_pullback is True
    assert row.heavy_conflict is True
    assert row.rvol == 1.6
    assert row.directional_efficiency == 0.68


def test_warmup_is_unavailable_not_neutral() -> None:
    replay = _replay(status="WARMUP", state="PARTICIPATION_PENDING")
    projection = project_participation_behavior(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.participation_trend is ParticipationTrend.UNAVAILABLE
    assert row.effort_result is EffortResultBehavior.UNAVAILABLE
    assert row.absorption is AbsorptionBehavior.UNAVAILABLE
    assert row.break_participation is BreakParticipationBehavior.UNAVAILABLE
    assert row.shock is ShockBehavior.UNAVAILABLE


def test_limited_data_is_unavailable_even_when_native_stage_is_confirmed() -> None:
    replay = _replay(data_quality="DATA_LIMITED", participation_stage="CONFIRMED")
    projection = project_participation_behavior(replay, available_at=_available_at)
    assert projection is not None
    row = projection.for_timeframe("4h")

    assert row.participation_trend is ParticipationTrend.UNAVAILABLE
    assert row.effort_result is EffortResultBehavior.UNAVAILABLE
    assert row.absorption is AbsorptionBehavior.UNAVAILABLE
    assert row.break_participation is BreakParticipationBehavior.UNAVAILABLE
    assert row.shock is ShockBehavior.UNAVAILABLE


def test_projection_knowledge_boundary_filters_future_fact() -> None:
    replay = _replay()
    projection = project_participation_behavior(replay, available_at=_available_at)
    assert projection is not None

    before = projection.available_at(NOW)
    after = projection.available_at(NOW + timedelta(minutes=1))

    assert before.timeframe_facts == ()
    assert len(after.timeframe_facts) == 1
