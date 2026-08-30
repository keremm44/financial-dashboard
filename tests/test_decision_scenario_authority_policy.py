from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.arbiter import (
    ArbiterSelection,
    ArbiterState,
    arbitrate_entry_scenarios,
)
from financial_dashboard.decision.eligibility import EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityState
from financial_dashboard.decision.scenario import (
    EntryScenarioAssessment,
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
    ScenarioUnknownReason,
    build_entry_scenario,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.target_path import TargetPathStatus


def _scenario(
    horizon: DecisionHorizon,
    *,
    stage: ScenarioStage,
    presence: ScenarioPresence = ScenarioPresence.PRESENT,
    kind: ScenarioKind = ScenarioKind.CONTINUATION,
    unknown_reason: ScenarioUnknownReason = ScenarioUnknownReason.NONE,
) -> EntryScenarioAssessment:
    return EntryScenarioAssessment(
        horizon=horizon,
        presence=presence,
        stage=stage,
        kind=kind,
        structural_direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        structural_regime=StructuralRegime.DIRECTIONAL,
        opportunity_state=OpportunityState.MODERATE,
        target_path_status=TargetPathStatus.READY,
        active_target_identity=None,
        eligibility_state=EligibilityState.ELIGIBLE,
        reasons=(),
        blockers=(),
        waiting_for=(),
        source_lineage=(),
        unknown_reason=unknown_reason,
    )


def test_opportunity_none_keeps_structural_long_scenario_present_but_developing():
    assessment = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        structural=SimpleNamespace(
            data_quality=ContextDataQuality.VALID,
            direction=StructuralDirection.LONG,
            thesis_state=ThesisState.INTACT,
            source_refs=(),
        ),
        structural_snapshot=SimpleNamespace(
            relation=HorizonRelation.ALIGNED,
            long_term=SimpleNamespace(direction=StructuralDirection.LONG),
        ),
        opportunity=SimpleNamespace(
            state=OpportunityState.NONE,
            room_atr=0.25,
            target_identity="TEST_TARGET",
            source_lineage=(),
        ),
        eligibility=SimpleNamespace(
            state=EligibilityState.ELIGIBLE,
            blockers=(),
            waiting_for=(),
        ),
    )
    path = SimpleNamespace(
        status=TargetPathStatus.READY,
        active_node=None,
        nodes=(),
    )
    market_state = SimpleNamespace(
        structural_map=SimpleNamespace(structural_regime=StructuralRegime.DIRECTIONAL)
    )

    result = build_entry_scenario(
        assessment,
        target_path=path,
        market_state=market_state,
    )

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.DEVELOPING
    assert result.kind is ScenarioKind.CONTINUATION
    assert "OBSERVED_DIRECTIONAL_ROOM_INSUFFICIENT" in result.reasons
    assert "MORE_DIRECTIONAL_ROOM" in result.waiting_for


def test_qualified_short_term_owns_trade_horizon_when_long_term_is_also_qualified():
    long_term = _scenario(DecisionHorizon.LONG_TERM, stage=ScenarioStage.QUALIFIED)
    short_term = _scenario(DecisionHorizon.SHORT_TERM, stage=ScenarioStage.QUALIFIED)

    result = arbitrate_entry_scenarios(long_term, short_term)

    assert result.state is ArbiterState.SELECTED
    assert result.selection is ArbiterSelection.SHORT_TERM
    assert result.selected_horizon is DecisionHorizon.SHORT_TERM
    assert result.selected_scenario is short_term
    assert result.suppressed_horizons == ()
    assert "SHORT_TERM_QUALIFIED_TRADE_HORIZON" in result.reasons
    assert "LONG_TERM_QUALIFIED_CONTEXT_RETAINED" in result.reasons


def test_developing_short_term_does_not_displace_qualified_long_term():
    long_term = _scenario(DecisionHorizon.LONG_TERM, stage=ScenarioStage.QUALIFIED)
    short_term = _scenario(DecisionHorizon.SHORT_TERM, stage=ScenarioStage.DEVELOPING)

    result = arbitrate_entry_scenarios(long_term, short_term)

    assert result.selection is ArbiterSelection.LONG_TERM
    assert result.selected_horizon is DecisionHorizon.LONG_TERM
    assert result.suppressed_horizons == (DecisionHorizon.SHORT_TERM,)


def test_developing_short_term_still_waits_when_long_term_structure_is_unresolved():
    long_term = _scenario(
        DecisionHorizon.LONG_TERM,
        stage=ScenarioStage.UNAVAILABLE,
        presence=ScenarioPresence.UNKNOWN,
        kind=ScenarioKind.NONE,
        unknown_reason=ScenarioUnknownReason.STRUCTURE_UNRESOLVED,
    )
    short_term = _scenario(DecisionHorizon.SHORT_TERM, stage=ScenarioStage.DEVELOPING)

    result = arbitrate_entry_scenarios(long_term, short_term)

    assert result.state is ArbiterState.WAITING_FOR_LONG_TERM_RESOLUTION
    assert result.selection is ArbiterSelection.UNRESOLVED
    assert result.selected_horizon is None
    assert result.waiting_for == ("LONG_TERM_STRUCTURAL_AUTHORITY_TO_RESOLVE",)
