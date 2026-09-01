import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import financial_dashboard.decision.engine as engine_module
from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermissionScope, PermittedSide
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.conflict import ConflictAssessment, ConflictState
from financial_dashboard.decision.coverage import CoverageAssessment, CoverageFamily
from financial_dashboard.decision.durability import DurabilityAssessment, DurabilityState
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import (
    EnvironmentAlignment,
    EnvironmentAssessment,
    EnvironmentRisk,
    assess_environment,
)
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


ROOT = Path(__file__).resolve().parents[1]
DECISION_DIFF_TOOL = ROOT / "tools" / "decision_diff.py"
TURN6_BASELINE = ROOT / "tests" / "fixtures" / "decision" / "turn6_horizon_decision_baseline.json"


def _decision_diff_tool():
    spec = importlib.util.spec_from_file_location("turn6_decision_diff", DECISION_DIFF_TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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

    def participation(side_value, projection, *, timeframe):
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


def _environment_ref(timeframe: str, regime: VolatilityRangeRegime) -> FactRef:
    return FactRef(
        domain=ContextDomain.VOLATILITY,
        fact_type="VOLATILITY_ENVIRONMENT",
        symbol="TEST",
        timeframe=timeframe,
        native_id=f"ENV:{timeframe}:{regime.value}",
        native_state=regime.value,
        origin_time=10,
        confirmed_at=10,
        available_at=10,
        lineage_id=f"ENV:{timeframe}",
        causal_family=CausalFamily.REGIME,
        source_family=SourceFamily.PRICE_DERIVED_INDICATOR,
        data_quality=ContextDataQuality.VALID,
    )


def _volatility_environment_projection(
    *,
    four_hour: VolatilityRangeRegime,
    one_hour: VolatilityRangeRegime,
):
    regimes = {"4h": four_hour, "1h": one_hour}

    def for_timeframe(timeframe: str):
        regime = regimes[timeframe]
        return SimpleNamespace(
            ref=_environment_ref(timeframe, regime),
            range_regime=regime,
            expansion_character=ExpansionCharacter.NEUTRAL,
            expansion_direction=0,
        )

    return SimpleNamespace(for_timeframe=for_timeframe)


def _use_real_environment_gate(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "assess_environment", assess_environment)
    monkeypatch.setattr(engine_module, "assess_eligibility", assess_eligibility)


def _baseline_event(snapshot, result, *, event_consumed: bool):
    return {
        "timestamp": str(snapshot.as_of),
        "action": result.final.action.value,
        "blockers": list(result.final.blockers),
        "waiting_for": list(result.final.waiting_for),
        "snapshot": {
            "entry_horizon": result.horizon.value,
            "execution": {
                "state": result.execution.state.value,
                "event_consumed": event_consumed,
            },
            "trade_lifecycle": {
                "position_state": "OPEN" if result.final.action is DecisionAction.BUY else "FLAT",
                "exit_stage": None,
            },
        },
    }


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
    assert calls["environment"] == ["4h", "4h"]
    assert calls["timing"] == ["1h", "30m"]
    assert calls["reaction"][0] == ("1d", "4h", "2h", "1h")
    assert calls["reaction"][2] == ("4h", "2h", "1h", "30m")


def test_short_term_environment_uses_4h_shock_while_structure_stays_1h(monkeypatch):
    _patch_pipeline(monkeypatch)
    _use_real_environment_gate(monkeypatch)
    snapshot = _snapshot()
    snapshot.volatility_environment = _volatility_environment_projection(
        four_hour=VolatilityRangeRegime.SHOCK,
        one_hour=VolatilityRangeRegime.BALANCED,
    )

    prepared = engine_module.prepare_horizon_assessment(snapshot, DecisionHorizon.SHORT_TERM)

    assert prepared.structural.authority_timeframe == "1h"
    assert prepared.environment.source_refs[0].timeframe == "4h"
    assert prepared.environment.risk is EnvironmentRisk.HARD_BLOCK
    assert prepared.eligibility.state is EligibilityState.BLOCKED
    assert "VOLATILITY_SHOCK" in prepared.eligibility.blockers


def test_short_term_does_not_reuse_1h_structure_row_as_environment_authority(monkeypatch):
    _patch_pipeline(monkeypatch)
    _use_real_environment_gate(monkeypatch)
    snapshot = _snapshot()
    snapshot.volatility_environment = _volatility_environment_projection(
        four_hour=VolatilityRangeRegime.BALANCED,
        one_hour=VolatilityRangeRegime.SHOCK,
    )

    prepared = engine_module.prepare_horizon_assessment(snapshot, DecisionHorizon.SHORT_TERM)

    assert prepared.structural.authority_timeframe == "1h"
    assert prepared.environment.source_refs[0].timeframe == "4h"
    assert prepared.environment.risk is EnvironmentRisk.NORMAL
    assert prepared.eligibility.state is EligibilityState.ELIGIBLE
    assert "VOLATILITY_SHOCK" not in prepared.eligibility.blockers


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


def test_finalize_uses_prepared_market_assessment_without_recomputing_upstream(monkeypatch):
    calls = {}
    _patch_pipeline(monkeypatch, calls=calls)
    snapshot = _snapshot()
    prepared = engine_module.prepare_horizon_assessment(snapshot, DecisionHorizon.SHORT_TERM)
    upstream_calls = {name: tuple(values) for name, values in calls.items()}
    event = ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=10,
        available_at=10,
        reason="FRESH_EVENT",
    )

    result = engine_module.finalize_horizon_assessment(
        snapshot,
        prepared,
        execution_event=event,
    )

    assert result.final.action is DecisionAction.BUY
    assert {name: tuple(values) for name, values in calls.items()} == upstream_calls
    assert result.structural is prepared.structural
    assert result.eligibility is prepared.eligibility


def test_prepare_finalize_matches_public_compatibility_wrapper(monkeypatch):
    _patch_pipeline(monkeypatch)
    snapshot = _snapshot()
    event = ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=10,
        available_at=10,
        reason="FRESH_EVENT",
    )

    prepared = engine_module.prepare_horizon_assessment(snapshot, DecisionHorizon.LONG_TERM)
    explicit = engine_module.finalize_horizon_assessment(
        snapshot,
        prepared,
        execution_event=event,
    )
    compatibility = engine_module.assess_horizon_decision(
        snapshot,
        DecisionHorizon.LONG_TERM,
        execution_event=event,
    )

    assert explicit == compatibility


def test_turn6_horizon_decision_fingerprint_matches_frozen_baseline(monkeypatch):
    _patch_pipeline(monkeypatch)
    actual = []
    for timestamp, horizon, with_event in (
        (10, DecisionHorizon.LONG_TERM, False),
        (11, DecisionHorizon.LONG_TERM, True),
        (12, DecisionHorizon.SHORT_TERM, False),
        (13, DecisionHorizon.SHORT_TERM, True),
    ):
        snapshot = _snapshot()
        snapshot.as_of = timestamp
        event = None
        if with_event:
            event = ExecutionTriggerEvent(
                state=ExecutionTriggerState.CONFIRMED,
                side=StructuralDirection.LONG,
                timeframe="30m",
                observed_at=timestamp,
                available_at=timestamp,
                reason="TURN6_BASELINE_EVENT",
            )
        result = engine_module.assess_horizon_decision(
            snapshot,
            horizon,
            execution_event=event,
        )
        actual.append(_baseline_event(snapshot, result, event_consumed=with_event))

    expected = json.loads(TURN6_BASELINE.read_text(encoding="utf-8"))["events"]
    report = _decision_diff_tool().compare_events(expected, actual)

    assert report.status == "UNCHANGED", report.to_payload()
    assert report.is_empty, report.to_payload()


def test_v1_execution_timeframe_cannot_be_silently_changed():
    try:
        engine_module.DecisionEngineConfig(execution_timeframe="1h")
    except ValueError as exc:
        assert "30m" in str(exc)
    else:
        raise AssertionError("v1 execution timeframe change should fail")
