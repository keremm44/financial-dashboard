from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.participation_behavior_projection import (
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationBehaviorProjection,
    ParticipationTrend,
    project_participation_behavior,
)
from financial_dashboard.decision.participation import (
    ParticipationState,
    assess_participation,
)
from financial_dashboard.decision.structural import StructuralDirection
from financial_dashboard.engines.volume_participation_final import (
    FinalParticipationState,
    VolumeParticipationEngine,
)


def _ref(quality=ContextDataQuality.VALID):
    return FactRef(
        ContextDomain.VOLUME,
        "PARTICIPATION_BEHAVIOR",
        "THYAO",
        "1h",
        "VOL:1",
        "TEST",
        1,
        1,
        1,
        "VOL:1",
        CausalFamily.PARTICIPATION,
        SourceFamily.VOLUME_SERIES,
        quality,
    )


def _projection(**overrides):
    values = dict(
        timeframe="1h",
        ref=_ref(),
        status="OK",
        evidence_direction=1,
        participation_trend=ParticipationTrend.CONFIRMED,
        effort_result=EffortResultBehavior.NEUTRAL,
        break_participation=BreakParticipationBehavior.NONE,
        participation_direction=1,
        break_direction=0,
        heavy_conflict=False,
        heavy_conflict_reasons=(),
        heavy_conflict_bars=0,
    )
    values.update(overrides)
    row = SimpleNamespace(**values)
    return SimpleNamespace(for_timeframe=lambda timeframe: row)


def test_fresh_heavy_conflict_remains_material():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(
            heavy_conflict=True,
            heavy_conflict_reasons=("DIRECTIONAL_PROXY",),
            heavy_conflict_bars=5,
        ),
        timeframe="1h",
        max_heavy_conflict_age_bars=24,
    )
    assert result.state is ParticipationState.OPPOSING
    assert result.heavy_conflict is True
    assert result.heavy_conflict_bars == 5
    assert "PARTICIPATION_HEAVY_CONFLICT" in result.reasons


def test_stale_heavy_conflict_downgrades_to_weak_not_material():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(
            heavy_conflict=True,
            heavy_conflict_reasons=("DIRECTIONAL_PROXY",),
            heavy_conflict_bars=662,
        ),
        timeframe="1h",
        max_heavy_conflict_age_bars=24,
    )
    assert result.state is ParticipationState.WEAK
    assert result.heavy_conflict is False
    assert result.heavy_conflict_bars == 662
    assert "PARTICIPATION_HEAVY_CONFLICT_STALE" in result.reasons


def test_heavy_conflict_without_age_policy_stays_material():
    result = assess_participation(
        StructuralDirection.LONG,
        _projection(heavy_conflict=True, heavy_conflict_bars=662),
        timeframe="1h",
    )
    assert result.state is ParticipationState.OPPOSING


def test_heavy_conflict_with_unknown_age_fails_conservative():
    projection = _projection(heavy_conflict=True, heavy_conflict_bars=None)
    result = assess_participation(
        StructuralDirection.LONG,
        projection,
        timeframe="1h",
        max_heavy_conflict_age_bars=24,
    )
    assert result.state is ParticipationState.OPPOSING


def test_engine_tracks_heavy_conflict_onset_reasons_and_age(monkeypatch):
    engine = VolumeParticipationEngine()
    metrics = SimpleNamespace(
        data_ready=True,
        up_confirmed=False,
        down_confirmed=False,
        up_candidate=False,
        down_candidate=False,
        volume_level=None,
        capital_level=None,
    )
    bar = {"open": 1, "high": 1, "low": 1, "close": 1}

    monkeypatch.setattr(
        engine, "_heavy_conflict_reasons", lambda m: ("DIRECTIONAL_PROXY",)
    )
    engine._rows.append(bar)
    engine._resolve_final(metrics, False)
    assert engine._last_heavy_conflict_reasons == ("DIRECTIONAL_PROXY",)
    assert engine._last_heavy_conflict_bars == 0

    engine._rows.append(bar)
    engine._resolve_final(metrics, False)
    assert engine._last_heavy_conflict_bars == 1

    monkeypatch.setattr(engine, "_heavy_conflict_reasons", lambda m: ())
    engine._rows.append(bar)
    engine._resolve_final(metrics, False)
    assert engine._last_heavy_conflict_reasons == ()
    assert engine._last_heavy_conflict_bars == 0


def test_engine_reasons_flow_into_final_export(monkeypatch):
    engine = VolumeParticipationEngine()
    metrics = SimpleNamespace(data_ready=True)
    monkeypatch.setattr(
        engine, "_heavy_conflict_reasons", lambda m: ("CAPITAL_PRICE",)
    )
    state = engine._resolve_final(metrics, False)
    assert state is FinalParticipationState.CONFLICT
    export = engine._build_final_export(state, engine._engine_direction(state), False, 0)
    assert export.heavy_conflict is True
    assert export.heavy_conflict_reasons == ("CAPITAL_PRICE",)
    assert export.heavy_conflict_bars == 0


def test_projection_maps_heavy_conflict_diagnostics():
    export = SimpleNamespace(
        heavy_conflict=True,
        heavy_conflict_reasons=("DIRECTIONAL_PROXY", "CAPITAL_PRICE"),
        heavy_conflict_bars=7,
        participation_stage="NONE",
        effort_result_class=None,
        absorption_stage="NONE",
        break_stage="NONE",
        one_bar_shock=False,
        participation_direction=0,
        controlled_pullback=False,
        controlled_reaction=False,
        absorption_side="NONE",
        break_direction=0,
        shock_direction=0,
        rvol=None,
        relative_traded_value=None,
        directional_value_pressure_5=None,
        directional_value_pressure_10=None,
        net_progress_atr=None,
        directional_efficiency=None,
    )
    latest = SimpleNamespace(
        audit_export=export,
        status="OK",
        state="PARTICIPATION_NEUTRAL",
        data_quality=ContextDataQuality.VALID,
        timestamp=1,
        evidence_direction=0,
    )
    replay = SimpleNamespace(
        symbol="THYAO",
        timeframes=("1h",),
        timeframe_replays=(SimpleNamespace(timeframe="1h", latest=latest),),
    )

    projection = project_participation_behavior(
        replay, available_at=lambda timestamp, timeframe: timestamp
    )
    assert isinstance(projection, ParticipationBehaviorProjection)
    row = projection.for_timeframe("1h")
    assert row.heavy_conflict is True
    assert row.heavy_conflict_reasons == ("DIRECTIONAL_PROXY", "CAPITAL_PRICE")
    assert row.heavy_conflict_bars == 7
