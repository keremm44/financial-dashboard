import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from financial_dashboard.decision.arbiter import arbitrate_entry_scenarios
from financial_dashboard.decision.composer import ActionSide, DecisionAction, FinalDecision
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.entry import compose_entry_decision
from financial_dashboard.decision.entry_qualification import EntryQualificationAssessment
from financial_dashboard.decision.execution import ExecutionTriggerState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
)
from financial_dashboard.decision.st_ownership import STEconomicOwnership
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPathStatus


ROOT = Path(__file__).resolve().parents[1]
DECISION_DIFF_TOOL = ROOT / "tools" / "decision_diff.py"


def _decision_diff_tool():
    spec = importlib.util.spec_from_file_location("turn5b_decision_diff", DECISION_DIFF_TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scenario(
    horizon,
    presence,
    *,
    stage=ScenarioStage.QUALIFIED,
    direction=StructuralDirection.LONG,
):
    if presence is ScenarioPresence.UNKNOWN:
        stage = ScenarioStage.UNAVAILABLE
    elif presence is ScenarioPresence.ABSENT:
        stage = ScenarioStage.NOT_APPLICABLE

    eligibility = EligibilityAssessment(
        (
            EligibilityState.ELIGIBLE
            if stage is ScenarioStage.QUALIFIED
            else EligibilityState.BLOCKED
            if stage is ScenarioStage.BLOCKED
            else EligibilityState.WAITING
        ),
        (),
        ("BLOCKED",) if stage is ScenarioStage.BLOCKED else (),
        ("WAIT_FOR_SETUP",) if stage is ScenarioStage.DEVELOPING else (),
    )
    target_path_status = (
        TargetPathStatus.READY
        if presence is ScenarioPresence.PRESENT
        else TargetPathStatus.UNKNOWN
        if presence is ScenarioPresence.UNKNOWN
        else TargetPathStatus.NO_OBSERVED_PATH
    )
    qualification = (
        EntryQualificationAssessment(
            state=stage,
            eligibility=eligibility,
            target_path_status=target_path_status,
            target_path_waiting_for=(),
            reasons=(),
        )
        if presence is ScenarioPresence.PRESENT
        else None
    )

    return EntryScenarioAssessment(
        horizon=horizon,
        presence=presence,
        kind=ScenarioKind.CONTINUATION if presence is ScenarioPresence.PRESENT else ScenarioKind.NONE,
        structural_direction=direction,
        thesis_state=ThesisState.INTACT,
        structural_regime=StructuralRegime.DIRECTIONAL,
        opportunity_state=(
            OpportunityState.MODERATE
            if presence is ScenarioPresence.PRESENT
            else OpportunityState.UNKNOWN
            if presence is ScenarioPresence.UNKNOWN
            else OpportunityState.NONE
        ),
        target_path_status=target_path_status,
        active_target_identity="T1" if presence is ScenarioPresence.PRESENT else None,
        eligibility=eligibility,
        qualification=qualification,
        reasons=("SCENARIO",) if presence is ScenarioPresence.PRESENT else (),
        presence_waiting_for=(),
        source_lineage=(f"{horizon.value}:scenario",),
    )


def _assessment(horizon, action, *, execution=ExecutionTriggerState.ABSENT):
    final = FinalDecision(
        horizon=horizon,
        market_side=StructuralDirection.LONG,
        action_side=ActionSide.LONG,
        action=action,
        eligibility=EligibilityState.ELIGIBLE,
        execution_trigger=execution,
        reasons=("FINAL",),
        blockers=(),
        waiting_for=("FRESH_EXECUTION_EVENT",) if action is DecisionAction.READY else (),
        source_lineage=(f"{horizon.value}:final",),
    )
    return SimpleNamespace(
        horizon=horizon,
        final=final,
        execution=SimpleNamespace(state=execution),
    )


def _diff_event(timestamp, decision):
    execution = None
    if decision.execution_state is not None:
        execution = {
            "state": decision.execution_state.value,
            "event_consumed": decision.execution_event_consumed,
        }
    return {
        "timestamp": timestamp,
        "action": decision.action.value,
        "blockers": list(decision.blockers),
        "waiting_for": list(decision.waiting_for),
        "snapshot": {
            "entry_horizon": (
                None if decision.selected_horizon is None else decision.selected_horizon.value
            ),
            "execution": execution,
            "trade_lifecycle": {"position_state": "FLAT", "exit_stage": None},
        },
    }


def _legacy_event(timestamp, action, horizon, *, blockers=(), waiting_for=()):
    return {
        "timestamp": timestamp,
        "action": action.value,
        "blockers": list(blockers),
        "waiting_for": list(waiting_for),
        "snapshot": {
            "entry_horizon": None if horizon is None else horizon.value,
            "execution": None,
            "trade_lifecycle": {"position_state": "FLAT", "exit_stage": None},
        },
    }


def test_qualified_st_bypasses_blocked_lt_and_reaches_ready():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT, stage=ScenarioStage.BLOCKED),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(DecisionHorizon.SHORT_TERM, DecisionAction.READY),
    )

    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.action is DecisionAction.READY
    assert result.blockers == ()
    assert result.waiting_for == ("FRESH_EXECUTION_EVENT",)


def test_qualified_st_bypasses_developing_lt_and_reaches_ready():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT, stage=ScenarioStage.DEVELOPING),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(DecisionHorizon.SHORT_TERM, DecisionAction.READY),
    )

    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.action is DecisionAction.READY


def test_qualified_st_can_proceed_when_lt_presence_is_unresolved():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
        short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(DecisionHorizon.SHORT_TERM, DecisionAction.READY),
    )

    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.action is DecisionAction.READY
    assert result.execution_event_consumed is False


def test_nonqualified_st_does_not_bypass_unresolved_lt_ownership():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN),
        _scenario(
            DecisionHorizon.SHORT_TERM,
            ScenarioPresence.PRESENT,
            stage=ScenarioStage.DEVELOPING,
        ),
    )

    result = compose_entry_decision(arbitration)

    assert result.selected_horizon is None
    assert result.action is DecisionAction.WAIT
    assert result.execution_event_consumed is False


def test_turn5b_controlled_decision_diff_captures_only_intended_override_rows():
    changed = []
    legacy = []

    cases = (
        (
            "blocked-lt",
            _scenario(
                DecisionHorizon.LONG_TERM,
                ScenarioPresence.PRESENT,
                stage=ScenarioStage.BLOCKED,
            ),
            _legacy_event(
                "blocked-lt",
                DecisionAction.NO_TRADE,
                DecisionHorizon.LONG_TERM,
                blockers=("BLOCKED",),
            ),
        ),
        (
            "developing-lt",
            _scenario(
                DecisionHorizon.LONG_TERM,
                ScenarioPresence.PRESENT,
                stage=ScenarioStage.DEVELOPING,
            ),
            _legacy_event(
                "developing-lt",
                DecisionAction.WAIT,
                DecisionHorizon.LONG_TERM,
                waiting_for=("WAIT_FOR_SETUP",),
            ),
        ),
        (
            "unknown-lt",
            _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.UNKNOWN),
            _legacy_event(
                "unknown-lt",
                DecisionAction.WAIT,
                None,
                waiting_for=("LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE",),
            ),
        ),
    )

    for timestamp, lt, legacy_event in cases:
        arbitration = arbitrate_entry_scenarios(
            lt,
            _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
            short_term_ownership=STEconomicOwnership.INDEPENDENT_ST,
        )
        decision = compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(
                DecisionHorizon.SHORT_TERM,
                DecisionAction.READY,
            ),
        )
        changed.append(_diff_event(timestamp, decision))
        legacy.append(legacy_event)

    report = _decision_diff_tool().compare_events(legacy, changed)

    assert report.status == "CHANGED"
    assert len(report.bar_changes) == 3
    assert report.classification_counts["ACTION CHANGED"] == 3
    assert report.classification_counts["SELECTED HORIZON CHANGED"] == 3
    assert report.action_timing_changes == ()
    assert all(item.after.action == DecisionAction.READY.value for item in report.bar_changes)
    assert all(item.after.selected_horizon == DecisionHorizon.SHORT_TERM.value for item in report.bar_changes)


def test_both_absent_produces_no_trade_not_a_synthetic_entry():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )

    result = compose_entry_decision(arbitration)

    assert result.action is DecisionAction.NO_TRADE
    assert result.selected_horizon is None


def test_both_qualified_keep_lt_tie_break_and_stop_at_ready_without_fresh_event():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(DecisionHorizon.LONG_TERM, DecisionAction.READY),
    )

    assert result.action is DecisionAction.READY
    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.execution_state is ExecutionTriggerState.ABSENT
    assert result.is_actionable_signal is False


def test_both_qualified_keep_lt_tie_break_and_confirmed_execution_can_emit_buy():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(
            DecisionHorizon.LONG_TERM,
            DecisionAction.BUY,
            execution=ExecutionTriggerState.CONFIRMED,
        ),
        execution_event_consumed=True,
    )

    assert result.action is DecisionAction.BUY
    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.execution_event_consumed is True
    assert result.is_actionable_signal is True


def test_explicit_lt_absence_still_allows_short_term_buy():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.ABSENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    result = compose_entry_decision(
        arbitration,
        selected_assessment=_assessment(
            DecisionHorizon.SHORT_TERM,
            DecisionAction.BUY,
            execution=ExecutionTriggerState.CONFIRMED,
        ),
        execution_event_consumed=True,
    )

    assert result.action is DecisionAction.BUY
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM


def test_non_qualified_selected_scenario_rejects_execution_assessment():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT, stage=ScenarioStage.DEVELOPING),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )

    with pytest.raises(ValueError):
        compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(DecisionHorizon.LONG_TERM, DecisionAction.BUY),
        )


def test_selected_assessment_must_match_arbiter_horizon():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.PRESENT),
    )

    with pytest.raises(ValueError):
        compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(DecisionHorizon.SHORT_TERM, DecisionAction.READY),
        )


def test_entry_layer_rejects_sell_or_hold_from_lower_level_composer():
    arbitration = arbitrate_entry_scenarios(
        _scenario(DecisionHorizon.LONG_TERM, ScenarioPresence.PRESENT),
        _scenario(DecisionHorizon.SHORT_TERM, ScenarioPresence.ABSENT),
    )

    with pytest.raises(ValueError):
        compose_entry_decision(
            arbitration,
            selected_assessment=_assessment(DecisionHorizon.LONG_TERM, DecisionAction.SELL),
        )
