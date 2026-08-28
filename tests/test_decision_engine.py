from types import SimpleNamespace

import financial_dashboard.decision.engine as engine_module
from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermissionScope, PermittedSide
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.conflict import ConflictAssessment, ConflictState
from financial_dashboard.decision.coverage import CoverageAssessment, CoverageFamily
from financial_dashboard.decision.durability import DurabilityAssessment, DurabilityState
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.environment import EnvironmentAlignment, EnvironmentAssessment, EnvironmentRisk
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.opportunity import OpportunityAssessment, OpportunityState
from financial_dashboard.decision.participation import ParticipationAssessment, ParticipationState
from financial_dashboard.decision.reaction import ReactionAssessment, ReactionState
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    HorizonStructuralSnapshot,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.timing import SetupTriggerAssessment, SetupTriggerState, TimingAssessment, TimingState
from financial_dashboard.context.volatility_environment_projection import ExpansionCharacter, VolatilityRangeRegime


def _structural(horizon, side=StructuralDirection.LONG):
    return StructuralAssessment(
        horizon=horizon,
        authority_timeframe="1d" if horizon is DecisionHorizon.LONG_TERM else "1h",
        direction=side,
        thesis_state=ThesisState.INTACT,
        native_state="BULLISH" if side is StructuralDirection.LONG else "BEARISH",
        transition_target=None,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(),
        reasons=("TEST",),
    )


def _permission(side=PermittedSide.LONG):
    return PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=side,
        gate_state=GateState.OPEN,
    )


def _snapshot(side=StructuralDirection.LONG):
    return SimpleNamespace(
        as_of=10,
        current_price=100.0,
        structure=object(),
        stabil_support=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        participation_behavior=None,
        volatility_environment=None,
        targeting=None,
        pattern_behavior=None,
        ham=None,
        liquidity_landscape=None,
        permission=_permission(PermittedSide.LONG if side is StructuralDirection.LONG else PermittedSide.SHORT),
        quality_for_timeframe=lambda timeframe: ContextDataQuality.VALID,
    )


def _patch_pipeline(monkeypatch, *, side=StructuralDirection.LONG, calls=None):
    lt = _structural(DecisionHorizon.LONG_TERM, side)
    st = _structural(DecisionHorizon.SHORT_TERM, side)
    monkeypatch.setattr(
        engine_module,
        "build_horizon_structural_snapshot",
        lambda structural: HorizonStructuralSnapshot(lt, st, HorizonRelation.ALIGNED, ("ALIGNED",)),
    )
    monkeypatch.setattr(
        engine_module,
        "_horizon_permission",
        lambda snapshot, horizon: _permission(PermittedSide.LONG if side is StructuralDirection.LONG else PermittedSide.SHORT),
    )
    monkeypatch.setattr(
        engine_module,
        "assess_durability",
        lambda stabil: DurabilityAssessment(DurabilityState.HEALTHY, ContextDataQuality.VALID, ("HEALTHY",), ()),
    )

    def reaction(side_value, **kwargs):
        if calls is not None:
            calls.setdefault("reaction", []).append(kwargs["timeframes"])
        return ReactionAssessment(ReactionState.CONFIRMED, False, True, False, ContextDataQuality.VALID, ("CONFIRMED",), ())

    def participation(side_value, projection, *, timeframe, max_heavy_conflict_age_bars=None):
        if calls is not None:
            calls.setdefault("participation", []).append(timeframe)
        return ParticipationAssessment(ParticipationState.SUPPORTIVE, False, False, ContextDataQuality.VALID, ("SUPPORTIVE",), ())

    def environment(side_value, projection, *, timeframe):
        if calls is not None:
            calls.setdefault("environment", []).append(timeframe)
        return EnvironmentAssessment(
            VolatilityRangeRegime.BALANCED,
            ExpansionCharacter.NEUTRAL,
            EnvironmentAlignment.NEUTRAL,
            EnvironmentRisk.NORMAL,
            ContextDataQuality.VALID,
            ("NORMAL",),
            (),
        )

    def timing(horizon, side_value, relation, *, reaction, pattern, timeframe):
        if calls is not None:
            calls.setdefault("timing", []).append(timeframe)
        setup = SetupTriggerAssessment(SetupTriggerState.CONFIRMED, timeframe, ("SETUP",), ())
        return TimingAssessment(TimingState.READY, timeframe, setup, ("READY",), (), ())

    monkeypatch.setattr(engine_module, "assess_reaction", reaction)
    monkeypatch.setattr(engine_module, "assess_participation", participation)
    monkeypatch.setattr(engine_module, "assess_environment", environment)
    monkeypatch.setattr(
        engine_module,
        "assess_opportunity",
        lambda side_value, targeting, *, calibration: OpportunityAssessment(OpportunityState.AMPLE, 2.0, "T1", "SUPPORTED", ("AMPLE",), ()),
    )
    monkeypatch.setattr(
        engine_module,
        "_coverage",
        lambda *args, **kwargs: CoverageAssessment(1.0, 1.0, (), (), (), tuple(CoverageFamily)),
    )
    monkeypatch.setattr(
        engine_module,
        "assess_conflict",
        lambda *args, **kwargs: ConflictAssessment(ConflictState.NONE, (), ("NONE",)),
    )
    monkeypatch.setattr(engine_module, "assess_timing", timing)
    monkeypatch.setattr(
        engine_module,
        "assess_eligibility",
        lambda *args, **kwargs: EligibilityAssessment(EligibilityState.ELIGIBLE, ("ELIGIBLE",), (), ()),
    )


def test_permission_policy_uses_actual_horizon_structural_authorities():
    assert engine_module._permission_policy(DecisionHorizon.LONG_TERM) == (
        "1d",
        ("4h", "2h", "1h"),
    )
    assert engine_module._permission_policy(DecisionHorizon.SHORT_TERM) == (
        "1h",
        ("30m",),
    )


def test_lt_and_st_use_distinct_role_aware_supporting_timeframes(monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls=calls)
    snapshot = _snapshot()

    engine_module.assess_horizon_decision(snapshot, DecisionHorizon.LONG_TERM)
    engine_module.assess_horizon_decision(snapshot, DecisionHorizon.SHORT_TERM)

    assert calls["participation"] == ["4h", "1h"]
    assert calls["environment"] == ["4h", "1h"]
    assert calls["timing"] == ["1h", "30m"]
    assert calls["reaction"][0] == ("1d", "4h", "2h", "1h")
    assert calls["reaction"][2] == ("4h", "2h", "1h", "30m")


def test_engine_stops_at_ready_without_fresh_execution_event(monkeypatch):
    _patch_pipeline(monkeypatch)
    result = engine_module.assess_horizon_decision(_snapshot(), DecisionHorizon.SHORT_TERM)
    assert result.final.action is DecisionAction.READY
    assert result.execution.state is ExecutionTriggerState.ABSENT


def test_engine_emits_buy_only_with_fresh_current_execution_event(monkeypatch):
    _patch_pipeline(monkeypatch)
    event = ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=10,
        available_at=10,
        reason="FRESH_EVENT",
    )
    result = engine_module.assess_horizon_decision(
        _snapshot(),
        DecisionHorizon.SHORT_TERM,
        execution_event=event,
    )
    assert result.final.action is DecisionAction.BUY


def test_v1_execution_timeframe_cannot_be_silently_changed():
    try:
        engine_module.DecisionEngineConfig(execution_timeframe="1h")
    except ValueError as exc:
        assert "30m" in str(exc)
    else:
        raise AssertionError("v1 execution timeframe change should fail")
