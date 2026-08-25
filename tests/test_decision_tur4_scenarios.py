from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermissionScope, PermittedSide
from financial_dashboard.context.volatility_environment_projection import ExpansionCharacter, VolatilityRangeRegime
from financial_dashboard.decision.composer import DecisionAction, compose_final_decision
from financial_dashboard.decision.conflict import ConflictAssessment, ConflictState
from financial_dashboard.decision.coverage import CoverageAssessment, CoverageFamily
from financial_dashboard.decision.eligibility import EligibilityState, assess_eligibility
from financial_dashboard.decision.environment import EnvironmentAlignment, EnvironmentAssessment, EnvironmentRisk
from financial_dashboard.decision.execution import ExecutionTriggerAssessment, ExecutionTriggerState
from financial_dashboard.decision.opportunity import OpportunityAssessment, OpportunityState
from financial_dashboard.decision.reaction import ReactionAssessment, ReactionState
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.timing import TimingState, assess_timing


def _structural(horizon, side=StructuralDirection.LONG, thesis=ThesisState.INTACT):
    return StructuralAssessment(
        horizon=horizon,
        authority_timeframe="1d" if horizon is DecisionHorizon.LONG_TERM else "1h",
        direction=side,
        thesis_state=thesis,
        native_state="BULLISH" if side is StructuralDirection.LONG else "BEARISH",
        transition_target=(StructuralDirection.SHORT if thesis is ThesisState.TRANSITIONING and side is StructuralDirection.LONG else None),
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(),
        reasons=("TEST",),
    )


def _reaction(state):
    return ReactionAssessment(
        state,
        failure_present=state is ReactionState.FAILED,
        confirmation_present=state is ReactionState.CONFIRMED,
        developing_present=state is ReactionState.DEVELOPING,
        data_quality=ContextDataQuality.VALID,
        reasons=(state.value,),
        source_refs=(),
    )


def _permission(side=PermittedSide.LONG):
    return PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=side,
        gate_state=GateState.OPEN,
    )


def _opportunity(state=OpportunityState.AMPLE):
    return OpportunityAssessment(state, 2.0, "T", "SUPPORTED", (state.value,), ())


def _environment(risk=EnvironmentRisk.NORMAL):
    return EnvironmentAssessment(
        VolatilityRangeRegime.BALANCED,
        ExpansionCharacter.NEUTRAL,
        EnvironmentAlignment.NEUTRAL,
        risk,
        ContextDataQuality.VALID,
        (risk.value,),
        (),
    )


def _coverage():
    return CoverageAssessment(1.0, 1.0, (), (), (), tuple(CoverageFamily))


def _execution(side, state=ExecutionTriggerState.ABSENT):
    return ExecutionTriggerAssessment(state, side, "30m", (state.value,), ())


def _eligibility(structural, timing, *, opportunity=None, conflict=None, environment=None, permission=None):
    return assess_eligibility(
        structural,
        permission=permission or _permission(PermittedSide.LONG if structural.direction is StructuralDirection.LONG else PermittedSide.SHORT),
        timing=timing,
        opportunity=opportunity or _opportunity(),
        conflict=conflict or ConflictAssessment(ConflictState.NONE, (), ("NONE",)),
        environment=environment or _environment(),
        coverage=_coverage(),
    )


def test_scenario_a_lt_bullish_st_counter_reaction_without_setup_stays_wait():
    structural = _structural(DecisionHorizon.LONG_TERM)
    timing = assess_timing(
        DecisionHorizon.LONG_TERM,
        StructuralDirection.LONG,
        HorizonRelation.COUNTER_REACTION,
        reaction=_reaction(ReactionState.ABSENT),
        pattern=None,
        timeframe="1h",
    )
    assert timing.state is TimingState.EARLY
    eligibility = _eligibility(structural, timing)
    final = compose_final_decision(structural, eligibility=eligibility, execution=_execution(StructuralDirection.LONG))
    assert final.action is DecisionAction.WAIT


def test_scenario_b_aligned_ready_with_low_conflict_can_buy_on_fresh_trigger():
    structural = _structural(DecisionHorizon.SHORT_TERM)
    timing = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.LONG,
        HorizonRelation.ALIGNED,
        reaction=_reaction(ReactionState.CONFIRMED),
        pattern=None,
        timeframe="30m",
    )
    assert timing.state is TimingState.READY
    eligibility = _eligibility(
        structural,
        timing,
        conflict=ConflictAssessment(ConflictState.LOW, (), ("PARTICIPATION_WEAK",)),
    )
    assert eligibility.state is EligibilityState.ELIGIBLE
    final = compose_final_decision(
        structural,
        eligibility=eligibility,
        execution=_execution(StructuralDirection.LONG, ExecutionTriggerState.CONFIRMED),
    )
    assert final.action is DecisionAction.BUY


def test_scenario_c_lt_transition_suspends_old_side_continuation():
    structural = _structural(DecisionHorizon.LONG_TERM, thesis=ThesisState.TRANSITIONING)
    timing = assess_timing(
        DecisionHorizon.LONG_TERM,
        StructuralDirection.LONG,
        HorizonRelation.EARLY_TRANSITION,
        reaction=_reaction(ReactionState.CONFIRMED),
        pattern=None,
        timeframe="1h",
    )
    eligibility = _eligibility(structural, timing)
    assert eligibility.state is EligibilityState.WAITING
    final = compose_final_decision(
        structural,
        eligibility=eligibility,
        execution=_execution(StructuralDirection.LONG, ExecutionTriggerState.CONFIRMED),
    )
    assert final.action is DecisionAction.WAIT


def test_scenario_f_compressed_room_is_wait_not_no_trade():
    structural = _structural(DecisionHorizon.SHORT_TERM)
    timing = SimpleNamespace(state=TimingState.READY, waiting_for=())
    eligibility = _eligibility(structural, timing, opportunity=_opportunity(OpportunityState.COMPRESSED))
    assert eligibility.state is EligibilityState.WAITING
    assert "MORE_DIRECTIONAL_ROOM" in eligibility.waiting_for


def test_scenario_g_elevated_unstable_environment_is_not_automatic_hard_gate():
    structural = _structural(DecisionHorizon.SHORT_TERM)
    timing = SimpleNamespace(state=TimingState.READY, waiting_for=())
    eligibility = _eligibility(structural, timing, environment=_environment(EnvironmentRisk.ELEVATED))
    assert eligibility.state is EligibilityState.ELIGIBLE
    final = compose_final_decision(structural, eligibility=eligibility, execution=_execution(StructuralDirection.LONG))
    assert final.action is DecisionAction.READY


def test_scenario_h_shock_is_no_trade_for_fresh_entry():
    structural = _structural(DecisionHorizon.SHORT_TERM)
    timing = SimpleNamespace(state=TimingState.READY, waiting_for=())
    eligibility = _eligibility(structural, timing, environment=_environment(EnvironmentRisk.HARD_BLOCK))
    final = compose_final_decision(structural, eligibility=eligibility, execution=_execution(StructuralDirection.LONG))
    assert final.action is DecisionAction.NO_TRADE


def test_scenario_k_valid_thesis_but_30m_execution_unavailable_is_wait():
    structural = _structural(DecisionHorizon.LONG_TERM)
    timing = SimpleNamespace(state=TimingState.READY, waiting_for=())
    eligibility = _eligibility(structural, timing)
    final = compose_final_decision(
        structural,
        eligibility=eligibility,
        execution=_execution(StructuralDirection.LONG, ExecutionTriggerState.UNAVAILABLE),
    )
    assert final.action is DecisionAction.WAIT
    assert final.market_side is StructuralDirection.LONG


def test_scenario_l_st_short_is_valid_market_state_but_not_auto_short_in_cash_v1():
    structural = _structural(DecisionHorizon.SHORT_TERM, StructuralDirection.SHORT)
    timing = SimpleNamespace(state=TimingState.READY, waiting_for=())
    eligibility = _eligibility(structural, timing)
    final = compose_final_decision(
        structural,
        eligibility=eligibility,
        execution=_execution(StructuralDirection.SHORT, ExecutionTriggerState.CONFIRMED),
    )
    assert final.market_side is StructuralDirection.SHORT
    assert final.action is DecisionAction.NO_TRADE
