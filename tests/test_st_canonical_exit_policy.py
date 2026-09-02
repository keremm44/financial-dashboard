from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.exit import (
    compose_position_exit_decision,
    transition_position_exit_lifecycle,
)
from financial_dashboard.decision.lifecycle import ExitStage, PositionState, TradeLifecycleState
from financial_dashboard.decision.position_metadata import PositionEntryMetadata, STTradeMemory
from financial_dashboard.decision.scenario import ScenarioKind
from financial_dashboard.decision.st_economic_history import STEconomicHistory
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.st_exit_policy import assess_st_canonical_exit
from financial_dashboard.decision.st_harvest import (
    STHarvestShadowAssessment,
    STHarvestShadowState,
    STHealthyBaseState,
)
from financial_dashboard.decision.st_thesis_identity import STEconomicMission, STThesisFamily
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.trade_exit import ExitExecutionState, PositionHealth


ENTRY = pd.Timestamp("2026-01-05 10:00")
NOW = pd.Timestamp("2026-01-05 12:00")


def _metadata():
    return PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=ENTRY,
        entry_price=100.0,
        active_target_identity=None,
        execution_timeframe="30m",
        execution_observed_at=ENTRY,
        execution_available_at=ENTRY,
        execution_reason="ENTRY_CONFIRMED",
        source_lineage=(),
        st_trade_memory=STTradeMemory(
            thesis_family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            initial_defended_anchor=None,
            initial_target_context=None,
        ),
    )


def _open_state(*, intent=None):
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:step8",
        entry_as_of=ENTRY,
        entry_metadata=_metadata(),
        st_economic_history=STEconomicHistory(),
        st_exit_intent=intent,
    )


def _shadow(
    state: STHarvestShadowState,
    *,
    healthy=STHealthyBaseState.ABSENT,
    reasons=("ECONOMIC_TEST_REASON",),
):
    return STHarvestShadowAssessment(
        state=state,
        thesis_family=STThesisFamily.BREAKOUT_ACCEPTANCE,
        mature=state not in {STHarvestShadowState.HOLD_MISSION_ACTIVE, STHarvestShadowState.UNRESOLVED},
        healthy_base_state=healthy,
        reasons=reasons,
        primary_evidence=("PRIMARY_EVIDENCE",),
        supporting_evidence=("SUPPORTING_EVIDENCE",),
        source_refs=(),
    )


def _patch_shadow(monkeypatch, assessment):
    monkeypatch.setattr(
        "financial_dashboard.decision.st_exit_policy.assess_st_harvest_shadow",
        lambda snapshot, state: assessment,
    )


def _snapshot():
    return SimpleNamespace(as_of=NOW, symbol="TEST")


def _event(*, state=ExecutionTriggerState.CONFIRMED):
    return ExecutionTriggerEvent(
        state=state,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=NOW,
        available_at=NOW,
        reason="EXIT_EVENT",
        source_refs=(),
    )


def test_thesis_specific_protective_precedence_becomes_canonical_terminal_exit(monkeypatch):
    shadow = _shadow(
        STHarvestShadowState.PROTECTIVE_PRECEDENCE,
        reasons=("ST_BREAKOUT_ACCEPTANCE_INVALIDATED",),
    )
    _patch_shadow(monkeypatch, shadow)

    result = assess_st_canonical_exit(_snapshot(), _open_state())

    assert result.exit_family is STExitFamily.PROTECTIVE_EXIT
    assert result.stage is ExitStage.EXIT_READY
    assert result.position_health is PositionHealth.PRESSURED
    assert result.reasons == ("ST_BREAKOUT_ACCEPTANCE_INVALIDATED",)
    assert result.waiting_for == ("FRESH_LONG_EXIT_EXECUTION_EVENT",)


def test_full_consumed_story_becomes_profit_harvest(monkeypatch):
    shadow = _shadow(
        STHarvestShadowState.PROFIT_HARVEST,
        reasons=("ST_FULL_CONSUMED_ECONOMIC_STORY_PRESENT",),
    )
    _patch_shadow(monkeypatch, shadow)

    result = assess_st_canonical_exit(_snapshot(), _open_state())

    assert result.exit_family is STExitFamily.PROFIT_HARVEST
    assert result.stage is ExitStage.EXIT_READY
    assert result.position_health is PositionHealth.PROTECTED


def test_real_healthy_base_remains_hold_and_preserves_nonterminal_stage(monkeypatch):
    shadow = _shadow(
        STHarvestShadowState.HOLD_HEALTHY_BASE,
        healthy=STHealthyBaseState.CONFIRMED,
        reasons=("ST_MATURE_TRADE_BUILDING_HEALTHY_BASE",),
    )
    _patch_shadow(monkeypatch, shadow)

    result = assess_st_canonical_exit(_snapshot(), _open_state())

    assert result.exit_family is None
    assert result.stage is ExitStage.MONITOR
    assert result.position_health is PositionHealth.HEALTHY
    assert "HOLD_HEALTHY_BASE" in result.reasons[0]


def test_unknown_or_insufficient_evidence_remains_hold_not_exit(monkeypatch):
    shadow = _shadow(
        STHarvestShadowState.UNRESOLVED,
        healthy=STHealthyBaseState.UNRESOLVED,
        reasons=("ST_HARVEST_THESIS_VALIDITY_UNRESOLVED",),
    )
    _patch_shadow(monkeypatch, shadow)

    result = assess_st_canonical_exit(_snapshot(), _open_state())

    assert result.exit_family is None
    assert result.stage is ExitStage.MONITOR
    assert result.position_health is PositionHealth.UNKNOWN
    assert result.waiting_for == ()


def test_committed_harvest_cannot_revert_to_hold_but_can_escalate(monkeypatch):
    from financial_dashboard.decision.lifecycle import transition_st_exit_intent

    harvested = transition_st_exit_intent(
        _open_state(),
        STExitFamily.PROFIT_HARVEST,
        as_of=ENTRY + pd.Timedelta(hours=1),
        reasons=("ORIGINAL_HARVEST_REASON",),
        source_lineage=("harvest:1",),
    )

    _patch_shadow(monkeypatch, _shadow(STHarvestShadowState.HOLD_PROGRESS))
    sticky = assess_st_canonical_exit(_snapshot(), harvested)
    assert sticky.exit_family is STExitFamily.PROFIT_HARVEST
    assert sticky.reasons == ("ORIGINAL_HARVEST_REASON",)
    assert sticky.source_lineage == ("harvest:1",)

    _patch_shadow(
        monkeypatch,
        _shadow(
            STHarvestShadowState.PROTECTIVE_PRECEDENCE,
            reasons=("ST_THESIS_INVALIDATED_AFTER_HARVEST",),
        ),
    )
    escalated = assess_st_canonical_exit(_snapshot(), harvested)
    assert escalated.exit_family is STExitFamily.PROTECTIVE_EXIT
    assert escalated.reasons == ("ST_THESIS_INVALIDATED_AFTER_HARVEST",)


def test_committed_protective_cannot_downgrade_to_harvest(monkeypatch):
    from financial_dashboard.decision.lifecycle import transition_st_exit_intent

    protective = transition_st_exit_intent(
        _open_state(),
        STExitFamily.PROTECTIVE_EXIT,
        as_of=ENTRY + pd.Timedelta(hours=1),
        reasons=("ORIGINAL_PROTECTIVE_REASON",),
        source_lineage=("protective:1",),
    )
    _patch_shadow(monkeypatch, _shadow(STHarvestShadowState.PROFIT_HARVEST))

    result = assess_st_canonical_exit(_snapshot(), protective)

    assert result.exit_family is STExitFamily.PROTECTIVE_EXIT
    assert result.reasons == ("ORIGINAL_PROTECTIVE_REASON",)
    assert result.source_lineage == ("protective:1",)


def test_st_compose_has_no_structure_only_fallback():
    with pytest.raises(ValueError, match="requires Step-8 economic assessment"):
        compose_position_exit_decision(
            _open_state(),
            SimpleNamespace(),
            as_of=NOW,
            channel_available=True,
        )


def test_terminal_economic_exit_arms_existing_execution_gate_without_step9_bypass(monkeypatch):
    _patch_shadow(
        monkeypatch,
        _shadow(
            STHarvestShadowState.PROTECTIVE_PRECEDENCE,
            reasons=("ST_FAILED_SELL_RECLAIM_INVALIDATED",),
        ),
    )
    economic = assess_st_canonical_exit(_snapshot(), _open_state())

    waiting = compose_position_exit_decision(
        _open_state(),
        SimpleNamespace(),
        as_of=NOW,
        channel_available=True,
        st_economic_exit=economic,
    )
    assert waiting.action is DecisionAction.HOLD
    assert waiting.stage is ExitStage.EXIT_READY
    assert waiting.execution.state is ExitExecutionState.ABSENT
    assert waiting.economic_exit_family is STExitFamily.PROTECTIVE_EXIT

    sold = compose_position_exit_decision(
        _open_state(),
        SimpleNamespace(),
        as_of=NOW,
        execution_event=_event(),
        channel_available=True,
        st_economic_exit=economic,
    )
    assert sold.action is DecisionAction.SELL
    assert sold.execution.state is ExitExecutionState.CONFIRMED
    assert sold.execution_event_consumed is True


def test_hold_policy_does_not_consume_premature_exit_event(monkeypatch):
    _patch_shadow(monkeypatch, _shadow(STHarvestShadowState.HOLD_PROGRESS))
    economic = assess_st_canonical_exit(_snapshot(), _open_state())

    decision = compose_position_exit_decision(
        _open_state(),
        SimpleNamespace(),
        as_of=NOW,
        execution_event=_event(),
        channel_available=True,
        st_economic_exit=economic,
    )

    assert decision.action is DecisionAction.HOLD
    assert decision.stage is ExitStage.MONITOR
    assert decision.execution.state is ExitExecutionState.NOT_ARMED
    assert decision.execution_event_consumed is False
    assert decision.economic_exit_family is None


def test_lifecycle_commits_terminal_reason_before_execution_and_copies_it_on_sell(monkeypatch):
    _patch_shadow(
        monkeypatch,
        _shadow(
            STHarvestShadowState.PROFIT_HARVEST,
            reasons=("ST_FULL_CONSUMED_ECONOMIC_STORY_PRESENT",),
        ),
    )
    state = _open_state()
    economic = assess_st_canonical_exit(_snapshot(), state)
    waiting = compose_position_exit_decision(
        state,
        SimpleNamespace(),
        as_of=NOW,
        channel_available=True,
        st_economic_exit=economic,
    )

    armed = transition_position_exit_lifecycle(state, waiting)
    assert armed.action is DecisionAction.HOLD
    assert armed.current.position is PositionState.OPEN
    assert armed.current.st_exit_intent is not None
    assert armed.current.st_exit_intent.family is STExitFamily.PROFIT_HARVEST
    assert armed.current.st_exit_intent.reasons == ("ST_FULL_CONSUMED_ECONOMIC_STORY_PRESENT",)

    later = NOW + pd.Timedelta(minutes=30)
    later_event = ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=later,
        available_at=later,
        reason="EXIT_EVENT_LATER",
        source_refs=(),
    )
    sticky_economic = SimpleNamespace(
        exit_family=STExitFamily.PROFIT_HARVEST,
        stage=ExitStage.EXIT_READY,
        position_health=PositionHealth.PROTECTED,
        reasons=armed.current.st_exit_intent.reasons,
        waiting_for=("FRESH_LONG_EXIT_EXECUTION_EVENT",),
        source_refs=(),
        source_lineage=armed.current.st_exit_intent.source_lineage,
    )
    sold = compose_position_exit_decision(
        armed.current,
        SimpleNamespace(),
        as_of=later,
        execution_event=later_event,
        channel_available=True,
        st_economic_exit=sticky_economic,
    )
    closed = transition_position_exit_lifecycle(armed.current, sold).current

    assert closed.position is PositionState.FLAT
    assert closed.last_closed_st_exit is not None
    assert closed.last_closed_st_exit.family is STExitFamily.PROFIT_HARVEST
    assert closed.last_closed_st_exit.reasons == ("ST_FULL_CONSUMED_ECONOMIC_STORY_PRESENT",)
