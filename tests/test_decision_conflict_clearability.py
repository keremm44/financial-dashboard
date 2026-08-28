from __future__ import annotations

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.volatility_environment_projection import (
    ExpansionCharacter,
    VolatilityRangeRegime,
)
from financial_dashboard.decision.conflict import ConflictState, assess_conflict
from financial_dashboard.decision.environment import (
    EnvironmentAlignment,
    EnvironmentAssessment,
    EnvironmentRisk,
)
from financial_dashboard.decision.participation import (
    ParticipationAssessment,
    ParticipationState,
)
from financial_dashboard.decision.reaction import (
    ReactionRelevancePolicy,
    assess_reaction,
    select_relevant_zones,
)
from financial_dashboard.decision.structural import StructuralDirection

from tests.test_decision_reaction_relevance import _ob, _ob_projection

_PRICE = 100.0
_POLICY = ReactionRelevancePolicy()


def _neutral_environment() -> EnvironmentAssessment:
    return EnvironmentAssessment(
        regime=VolatilityRangeRegime.BALANCED,
        character=ExpansionCharacter.NEUTRAL,
        alignment=EnvironmentAlignment.NEUTRAL,
        risk=EnvironmentRisk.NORMAL,
        data_quality=ContextDataQuality.VALID,
        reasons=(),
        source_refs=(),
    )


def _neutral_participation() -> ParticipationAssessment:
    return ParticipationAssessment(
        ParticipationState.NEUTRAL,
        False,
        False,
        ContextDataQuality.VALID,
        (),
        (),
    )


def _conflict_for(ob_projection, *, policy: ReactionRelevancePolicy | None):
    scoped_ob, _scoped_fvg = (
        select_relevant_zones(ob_projection, None, current_price=_PRICE, policy=policy)
        if policy is not None
        else (ob_projection, None)
    )
    reaction = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=scoped_ob,
        timeframes=("1h",),
        relevance=policy,
    )
    return assess_conflict(
        StructuralDirection.LONG,
        reaction=reaction,
        participation=_neutral_participation(),
        environment=_neutral_environment(),
    )


def test_material_conflict_clears_when_failure_ages_out():
    # A young terminal failure keeps MATERIAL conflict alive ...
    young = _ob_projection(
        _ob(
            native_id="OB:fail:LONG",
            state="CONSUMED",
            active=False,
            interaction="FAILED",
            age_bars=10,
        )
    )
    assert _conflict_for(young, policy=_POLICY).state is ConflictState.MATERIAL

    # ... and the very same zone stops voting once it exceeds the age bound.
    aged = _ob_projection(
        _ob(
            native_id="OB:fail:LONG",
            state="CONSUMED",
            active=False,
            interaction="FAILED",
            age_bars=10_000,
        )
    )
    assert _conflict_for(aged, policy=_POLICY).state is ConflictState.NONE


def test_pathologic_all_history_failure_stays_material_without_policy():
    # Regression documentation for the pre-fix behaviour (KN-1): without the
    # relevance scope, a single ancient terminal failure keeps MATERIAL conflict
    # alive forever.
    legacy = _ob_projection(
        _ob(
            native_id="OB:ancient:LONG",
            state="CONSUMED",
            active=False,
            interaction="FAILED",
            age_bars=10_000,
        )
    )
    assert _conflict_for(legacy, policy=None).state is ConflictState.MATERIAL


def test_thousand_terminal_failures_reduce_to_age_window():
    zones = tuple(
        _ob(
            native_id=f"OB:{age}:LONG",
            state="CONSUMED",
            active=False,
            interaction="FAILED",
            age_bars=age,
        )
        for age in range(1, 1001)
    )
    projection = _ob_projection(*zones)

    scoped_ob, _ = select_relevant_zones(
        projection, None, current_price=_PRICE, policy=_POLICY
    )
    assert len(scoped_ob.observations) == _POLICY.max_age_bars
    ages = sorted(item.age_bars for item in scoped_ob.observations)
    assert ages[-1] <= _POLICY.max_age_bars

    conflict = _conflict_for(projection, policy=_POLICY)
    # Young failures still vote: MATERIAL survives but only from the recent window.
    assert conflict.state is ConflictState.MATERIAL
    reaction_family = next(
        family for family in conflict.families if family.family == "REACTION"
    )
    assert len(reaction_family.lineage_ids) == _POLICY.max_age_bars
