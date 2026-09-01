from __future__ import annotations

import pytest

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.stabil_interpretation import (
    StabilHorizonAssessment,
    StabilHorizonState,
)
from financial_dashboard.decision.stabil_policy import (
    StabilPolicyEffect,
    assess_stabil_entry_policy,
    policy_effect_for_row,
)
from financial_dashboard.decision.structural import DecisionHorizon


LT_EXPECTED = {
    StabilHorizonState.UNKNOWN: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.NOT_ESTABLISHED: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.ADVANCING_FOUNDATION: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.STABLE_FOUNDATION: StabilPolicyEffect.NEUTRAL,
    StabilHorizonState.LAGGING_FOUNDATION: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.FALLING_FOUNDATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.SUPPORT_TESTING: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.HOLDING_ABOVE: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.DISTANT_ABOVE: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.RANGE_AROUND_SUPPORT: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.BREAKDOWN_ATTEMPT: StabilPolicyEffect.WAIT,
    StabilHorizonState.BREAKDOWN_ACCEPTED: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.DOWNSIDE_CONTINUATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.RECLAIMING: StabilPolicyEffect.WAIT,
    StabilHorizonState.RECOVERY_CONFIRMED: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.RECOVERY_FAILED: StabilPolicyEffect.HARD_CONTRADICTION,
}

ST_EXPECTED = {
    StabilHorizonState.UNKNOWN: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.NOT_ESTABLISHED: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.ADVANCING_FOUNDATION: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.STABLE_FOUNDATION: StabilPolicyEffect.NEUTRAL,
    StabilHorizonState.LAGGING_FOUNDATION: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.FALLING_FOUNDATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.SUPPORT_TESTING: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.HOLDING_ABOVE: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.DISTANT_ABOVE: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.RANGE_AROUND_SUPPORT: StabilPolicyEffect.NEUTRAL,
    StabilHorizonState.BREAKDOWN_ATTEMPT: StabilPolicyEffect.WAIT,
    StabilHorizonState.BREAKDOWN_ACCEPTED: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.DOWNSIDE_CONTINUATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.RECLAIMING: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.RECOVERY_CONFIRMED: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.RECOVERY_FAILED: StabilPolicyEffect.HARD_CONTRADICTION,
}


def _assessment(
    horizon: DecisionHorizon,
    state: StabilHorizonState,
    *,
    validity: str = "ACTIVE",
    progression: str = "SAME",
    motion: str = "FLAT",
    relation: str = "ABOVE_NEAR",
    interaction: str = "HOLDING_ABOVE",
) -> StabilHorizonAssessment:
    return StabilHorizonAssessment(
        horizon=horizon,
        state=state,
        data_quality=ContextDataQuality.VALID,
        validity=validity,
        dynamics="FLAT",
        progression=progression,
        motion=motion,
        relation=relation,
        interaction=interaction,
        approach_origin="FROM_ABOVE",
        support_level=100.0,
        support_floor=98.0,
        distance_atr=1.0,
        distance_delta_atr=0.0,
        bars_above_support=4,
        bars_below_support=0,
        reclaim_count=0,
        bars_since_rebase=5,
        cross_count=0,
        last_rebase_step_atr=None,
        reclaim_active=False,
        event_types=(),
        reasons=("TEST",),
        source_refs=(),
    )


@pytest.mark.parametrize(
    ("horizon", "expected"),
    (
        (DecisionHorizon.LONG_TERM, LT_EXPECTED),
        (DecisionHorizon.SHORT_TERM, ST_EXPECTED),
    ),
)
def test_every_approved_matrix_row_has_exact_horizon_effect(horizon, expected) -> None:
    assert set(expected) == set(StabilHorizonState)
    assert {
        row: policy_effect_for_row(horizon, row)
        for row in StabilHorizonState
    } == expected


@pytest.mark.parametrize("horizon", tuple(DecisionHorizon))
def test_falling_foundation_wins_over_reclaiming(horizon) -> None:
    result = assess_stabil_entry_policy(
        _assessment(
            horizon,
            StabilHorizonState.RECLAIMING,
            progression="REBASED_LOWER",
            motion="FALLING",
            interaction="RECLAIM_ATTEMPT",
        )
    )

    assert result.effect is StabilPolicyEffect.HARD_CONTRADICTION
    assert StabilHorizonState.FALLING_FOUNDATION in result.winning_rows
    assert StabilHorizonState.RECLAIMING in {item.row for item in result.contributions}


@pytest.mark.parametrize("horizon", tuple(DecisionHorizon))
def test_breakdown_attempt_wins_over_distance_risk(horizon) -> None:
    result = assess_stabil_entry_policy(
        _assessment(
            horizon,
            StabilHorizonState.BREAKDOWN_ATTEMPT,
            relation="ABOVE_FAR",
            interaction="BREAKDOWN_ATTEMPT",
        )
    )

    assert result.effect is StabilPolicyEffect.WAIT
    assert StabilHorizonState.BREAKDOWN_ATTEMPT in result.winning_rows
    assert StabilHorizonState.DISTANT_ABOVE in {item.row for item in result.contributions}


@pytest.mark.parametrize("horizon", tuple(DecisionHorizon))
def test_lagging_risk_wins_over_recovery_support(horizon) -> None:
    result = assess_stabil_entry_policy(
        _assessment(
            horizon,
            StabilHorizonState.RECOVERY_CONFIRMED,
            motion="FLAT",
            relation="ABOVE_FAR",
            interaction="RECOVERY_CONFIRMED",
        )
    )

    assert result.effect is StabilPolicyEffect.RISK_CONTEXT
    rows = {item.row for item in result.contributions}
    assert StabilHorizonState.RECOVERY_CONFIRMED in rows
    assert StabilHorizonState.LAGGING_FOUNDATION in rows


@pytest.mark.parametrize("horizon", tuple(DecisionHorizon))
def test_below_floor_subsumes_reclaim_and_remains_hard_contradiction(horizon) -> None:
    result = assess_stabil_entry_policy(
        _assessment(
            horizon,
            StabilHorizonState.RECLAIMING,
            validity="BELOW_FLOOR",
            interaction="RECLAIM_ATTEMPT",
        )
    )

    assert result.effect is StabilPolicyEffect.HARD_CONTRADICTION
    assert StabilHorizonState.DOWNSIDE_CONTINUATION in result.winning_rows


@pytest.mark.parametrize("horizon", tuple(DecisionHorizon))
def test_breached_subsumes_supportive_facts_to_at_least_wait(horizon) -> None:
    result = assess_stabil_entry_policy(
        _assessment(
            horizon,
            StabilHorizonState.ADVANCING_FOUNDATION,
            validity="BREACHED",
            progression="REBASED_HIGHER",
            motion="RISING",
            relation="ABOVE_NEAR",
            interaction="SUPPORTED_ADVANCE",
        )
    )

    assert result.effect is StabilPolicyEffect.WAIT
    assert StabilHorizonState.BREAKDOWN_ATTEMPT in result.winning_rows


@pytest.mark.parametrize(
    "interaction",
    ("BREAKDOWN_ACCEPTED", "RECOVERY_FAILED", "DOWNSIDE_CONTINUATION"),
)
@pytest.mark.parametrize("horizon", tuple(DecisionHorizon))
def test_breached_with_heavier_native_interaction_remains_hard(horizon, interaction) -> None:
    state = {
        "BREAKDOWN_ACCEPTED": StabilHorizonState.BREAKDOWN_ACCEPTED,
        "RECOVERY_FAILED": StabilHorizonState.RECOVERY_FAILED,
        "DOWNSIDE_CONTINUATION": StabilHorizonState.DOWNSIDE_CONTINUATION,
    }[interaction]
    result = assess_stabil_entry_policy(
        _assessment(
            horizon,
            state,
            validity="BREACHED",
            interaction=interaction,
        )
    )

    assert result.effect is StabilPolicyEffect.HARD_CONTRADICTION


def test_policy_result_has_no_action_or_exit_authority() -> None:
    result = assess_stabil_entry_policy(
        _assessment(
            DecisionHorizon.LONG_TERM,
            StabilHorizonState.FALLING_FOUNDATION,
            motion="FALLING",
        )
    )

    assert not hasattr(result, "action")
    assert not hasattr(result, "blockers")
    assert not hasattr(result, "waiting_for")
