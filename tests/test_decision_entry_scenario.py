from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.market_state import StructuralRegime
from financial_dashboard.decision.opportunity import OpportunityAssessment, OpportunityState
from financial_dashboard.decision.scenario import (
    ScenarioKind,
    ScenarioPresence,
    ScenarioStage,
    build_entry_scenario,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.target_path import (
    NativePathDisposition,
    TargetPath,
    TargetPathNode,
    TargetPathNodeState,
    TargetPathRole,
    TargetPathSource,
    TargetPathStatus,
)


def _path(*, state=TargetPathNodeState.ACTIVE, status=TargetPathStatus.READY):
    nodes = ()
    if status is TargetPathStatus.READY:
        nodes = (
            TargetPathNode(
                identity="T1",
                direction=StructuralDirection.LONG,
                low=105.0,
                high=105.0,
                anchor_price=105.0,
                distance_price=5.0,
                distance_atr=1.0,
                roles=(TargetPathRole.OBJECTIVE,),
                sources=(TargetPathSource.LIQUIDITY,),
                timeframes=("1h",),
                source_keys=("liq-1",),
                lineage_ids=("lineage-1",),
                source_refs=(),
                native_states=("ACTIVE",),
                native_disposition=(
                    NativePathDisposition.DEFENDED
                    if state is TargetPathNodeState.DEFENDED
                    else NativePathDisposition.PENDING
                ),
                state=state,
            ),
        )
    return TargetPath(
        symbol="TEST",
        as_of=1,
        direction=StructuralDirection.LONG,
        current_price=100.0,
        status=status,
        nodes=nodes,
        thesis_boundaries=(),
        reasons=(),
    )


def _assessment(
    *,
    horizon=DecisionHorizon.LONG_TERM,
    direction=StructuralDirection.LONG,
    thesis=ThesisState.INTACT,
    quality=ContextDataQuality.VALID,
    opportunity=OpportunityState.MODERATE,
    room_atr=1.5,
    eligibility=EligibilityState.ELIGIBLE,
    blockers=(),
    waiting=(),
    relation=HorizonRelation.ALIGNED,
    lt_direction=StructuralDirection.LONG,
):
    structural = SimpleNamespace(
        direction=direction,
        thesis_state=thesis,
        data_quality=quality,
        source_refs=(),
    )
    return SimpleNamespace(
        horizon=horizon,
        structural=structural,
        structural_snapshot=SimpleNamespace(
            relation=relation,
            long_term=SimpleNamespace(direction=lt_direction),
        ),
        opportunity=OpportunityAssessment(
            opportunity,
            room_atr,
            "target-1" if room_atr is not None else None,
            "SUPPORTED" if room_atr is not None else None,
            (),
            (),
        ),
        eligibility=EligibilityAssessment(eligibility, (), blockers, waiting),
    )


def _market(regime=StructuralRegime.DIRECTIONAL):
    return SimpleNamespace(structural_map=SimpleNamespace(structural_regime=regime))


def test_observed_long_scenario_can_be_qualified_without_emitting_action():
    result = build_entry_scenario(_assessment(), target_path=_path(), market_state=_market())

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.QUALIFIED
    assert result.kind is ScenarioKind.CONTINUATION
    assert result.active_target_identity == "T1"
    assert result.owns_horizon is True
    assert not hasattr(result, "action")


def test_blocked_lt_scenario_remains_present_instead_of_disappearing():
    result = build_entry_scenario(
        _assessment(
            eligibility=EligibilityState.BLOCKED,
            blockers=("VOLATILITY_SHOCK",),
        ),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.BLOCKED
    assert result.blockers == ("VOLATILITY_SHOCK",)


def test_opportunity_none_means_scenario_absent():
    result = build_entry_scenario(
        _assessment(
            opportunity=OpportunityState.NONE,
            room_atr=0.1,
            eligibility=EligibilityState.BLOCKED,
            blockers=("OPPORTUNITY_NONE",),
        ),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.presence is ScenarioPresence.ABSENT
    assert result.stage is ScenarioStage.NOT_APPLICABLE


def test_canonical_short_structure_is_not_converted_to_long_entry_scenario():
    result = build_entry_scenario(
        _assessment(direction=StructuralDirection.SHORT),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.presence is ScenarioPresence.ABSENT
    assert "LONG_ENTRY_REQUIRES_LONG_STRUCTURE" in result.reasons


def test_unresolved_structure_is_unknown_not_absent():
    result = build_entry_scenario(
        _assessment(
            direction=StructuralDirection.UNRESOLVED,
            thesis=ThesisState.UNRESOLVED,
            opportunity=OpportunityState.UNKNOWN,
            room_atr=None,
            eligibility=EligibilityState.BLOCKED,
        ),
        target_path=_path(status=TargetPathStatus.UNKNOWN),
        market_state=_market(StructuralRegime.UNRESOLVED),
    )

    assert result.presence is ScenarioPresence.UNKNOWN
    assert result.stage is ScenarioStage.UNAVAILABLE


def test_no_observed_opportunity_is_unknown_and_never_clear_path_assumption():
    result = build_entry_scenario(
        _assessment(
            opportunity=OpportunityState.UNKNOWN,
            room_atr=None,
            eligibility=EligibilityState.WAITING,
            waiting=("OPPORTUNITY_EVIDENCE_OR_CALIBRATION",),
        ),
        target_path=_path(status=TargetPathStatus.NO_OBSERVED_PATH),
        market_state=_market(),
    )

    assert result.presence is ScenarioPresence.UNKNOWN
    assert "NO_OBSERVED_OPPORTUNITY_DOES_NOT_PROVE_ABSENCE" in result.reasons


def test_uncalibrated_but_observed_room_is_present_and_developing():
    result = build_entry_scenario(
        _assessment(
            opportunity=OpportunityState.UNKNOWN,
            room_atr=1.25,
            eligibility=EligibilityState.WAITING,
            waiting=("OPPORTUNITY_EVIDENCE_OR_CALIBRATION",),
        ),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.DEVELOPING


def test_transitioning_long_scenario_keeps_presence_but_cannot_be_qualified():
    result = build_entry_scenario(
        _assessment(
            thesis=ThesisState.TRANSITIONING,
            eligibility=EligibilityState.WAITING,
            waiting=("STRUCTURAL_TRANSITION_TO_RESOLVE",),
        ),
        target_path=_path(),
        market_state=_market(StructuralRegime.TRANSITION),
    )

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.DEVELOPING
    assert result.kind is ScenarioKind.TRANSITION_CONTEXT


def test_defended_active_path_node_forces_developing_even_if_legacy_eligibility_is_eligible():
    result = build_entry_scenario(
        _assessment(),
        target_path=_path(state=TargetPathNodeState.DEFENDED),
        market_state=_market(),
    )

    assert result.presence is ScenarioPresence.PRESENT
    assert result.stage is ScenarioStage.DEVELOPING
    assert "ACTIVE_TARGET_PATH_NODE_DEFENDED" in result.waiting_for


def test_st_long_against_non_long_lt_is_described_as_standalone_not_promoted_to_lt():
    result = build_entry_scenario(
        _assessment(
            horizon=DecisionHorizon.SHORT_TERM,
            lt_direction=StructuralDirection.SHORT,
        ),
        target_path=_path(),
        market_state=_market(),
    )

    assert result.kind is ScenarioKind.SHORT_TERM_STANDALONE
    assert result.horizon is DecisionHorizon.SHORT_TERM
