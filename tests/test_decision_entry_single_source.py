from types import SimpleNamespace

import financial_dashboard.decision.arbiter as arbiter_module
import financial_dashboard.decision.engine as engine_module
import financial_dashboard.decision.entry as entry_module
from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.composer import ActionSide, DecisionAction, FinalDecision
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.entry_qualification import EntryQualificationAssessment
from financial_dashboard.decision.execution import ExecutionTriggerState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    PreparedEntryScenario,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
)
from financial_dashboard.decision.st_ownership import STEconomicOwnership
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection, ThesisState
from financial_dashboard.decision.target_path import TargetPathStatus


def _scenario(horizon):
    eligibility = EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        (),
        (),
        (),
    )
    qualification = EntryQualificationAssessment(
        state=ScenarioStage.QUALIFIED,
        eligibility=eligibility,
        target_path_status=TargetPathStatus.READY,
        target_path_waiting_for=(),
        reasons=(),
    )
    return EntryScenarioAssessment(
        horizon=horizon,
        presence=ScenarioPresence.PRESENT,
        kind=ScenarioKind.CONTINUATION,
        structural_direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        structural_regime=StructuralRegime.DIRECTIONAL,
        opportunity_state=OpportunityState.MODERATE,
        target_path_status=TargetPathStatus.READY,
        active_target_identity=f"{horizon.value}:target",
        eligibility=eligibility,
        qualification=qualification,
        reasons=("OBSERVED_LONG_ENTRY_SCENARIO",),
        presence_waiting_for=(),
        source_lineage=(f"{horizon.value}:scenario",),
    )


def _prepared(horizon):
    return PreparedEntryScenario(
        scenario=_scenario(horizon),
        assessment=SimpleNamespace(
            horizon=horizon,
            structural=SimpleNamespace(
                direction=StructuralDirection.LONG,
                data_quality=ContextDataQuality.VALID,
                source_refs=(),
            ),
        ),
    )


def _finalized(horizon):
    final = FinalDecision(
        horizon=horizon,
        market_side=StructuralDirection.LONG,
        action_side=ActionSide.LONG,
        action=DecisionAction.BUY,
        eligibility=EligibilityState.ELIGIBLE,
        execution_trigger=ExecutionTriggerState.CONFIRMED,
        reasons=("MARKET_ELIGIBLE_AND_FRESH_EXECUTION_EVENT_CONFIRMED",),
        blockers=(),
        waiting_for=(),
        source_lineage=(f"{horizon.value}:final",),
    )
    return SimpleNamespace(
        horizon=horizon,
        final=final,
        execution=SimpleNamespace(state=ExecutionTriggerState.CONFIRMED),
    )


def test_qualified_entry_prepares_each_horizon_once_and_finalizes_only_selected(monkeypatch):
    prepared = {
        DecisionHorizon.LONG_TERM: _prepared(DecisionHorizon.LONG_TERM),
        DecisionHorizon.SHORT_TERM: _prepared(DecisionHorizon.SHORT_TERM),
    }
    prepare_calls = []
    finalize_calls = []

    def prepare_entry_scenario(snapshot, horizon, *, config=None):
        prepare_calls.append(horizon)
        return prepared[horizon]

    def finalize_horizon_assessment(snapshot, assessment, *, execution_event=None):
        finalize_calls.append(assessment.horizon)
        return _finalized(assessment.horizon)

    monkeypatch.setattr(arbiter_module, "prepare_entry_scenario", prepare_entry_scenario)
    monkeypatch.setattr(
        arbiter_module,
        "classify_st_economic_ownership",
        lambda snapshot, long_term, short_term: STEconomicOwnership.UNRESOLVED,
    )
    monkeypatch.setattr(engine_module, "finalize_horizon_assessment", finalize_horizon_assessment)
    monkeypatch.setattr(
        engine_module,
        "assess_horizon_decision",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("selected entry path must not rebuild the full horizon assessment")
        ),
    )

    result = entry_module.assess_entry_decision(
        object(),
        execution_event=object(),
    )

    assert prepare_calls == [DecisionHorizon.LONG_TERM, DecisionHorizon.SHORT_TERM]
    assert finalize_calls == [DecisionHorizon.LONG_TERM]
    assert result.action is DecisionAction.BUY
    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.execution_event_consumed is True
