from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.stabil_authority import (
    StabilDecisionState,
    assess_stabil_authority,
)


def _stabil(
    interaction: str,
    *,
    motion: str = "FLAT_AFTER_FALL",
    relation: str = "ABOVE_FAR",
    validity: str = "HELD",
    quality: ContextDataQuality = ContextDataQuality.VALID,
):
    return SimpleNamespace(
        data_quality=quality,
        support_ref=None,
        events=(),
        validity=validity,
        behavior=SimpleNamespace(
            interaction=interaction,
            motion=motion,
            relation=relation,
        ),
    )


def test_confirmed_recovery_becomes_early_long_authority() -> None:
    result = assess_stabil_authority(_stabil("RECOVERY_CONFIRMED"))

    assert result.state is StabilDecisionState.RECOVERY_CONFIRMED
    assert result.recovery_confirmed
    assert result.usable
    assert not result.opposes_early_long


def test_data_limited_confirmed_recovery_remains_observed_authority() -> None:
    result = assess_stabil_authority(
        _stabil("RECOVERY_CONFIRMED", quality=ContextDataQuality.DATA_LIMITED)
    )

    assert result.state is StabilDecisionState.RECOVERY_CONFIRMED
    assert result.data_quality is ContextDataQuality.DATA_LIMITED
    assert result.recovery_confirmed
    assert result.usable
    assert "STABIL_AUTHORITY_DATA_LIMITED_BUT_OBSERVED" in result.reasons


def test_breakdown_attempt_is_warning_not_confirmed_breakdown() -> None:
    result = assess_stabil_authority(
        _stabil("BREAKDOWN_ATTEMPT", relation="BELOW_NEAR")
    )

    assert result.state is StabilDecisionState.BREAKDOWN_DEVELOPING
    assert result.breakdown_developing
    assert not result.breakdown_confirmed
    assert result.opposes_early_long


def test_failed_recovery_is_developing_breakdown_not_confirmed_breakdown() -> None:
    result = assess_stabil_authority(
        _stabil("RECOVERY_FAILED", relation="BELOW_NEAR")
    )

    assert result.state is StabilDecisionState.BREAKDOWN_DEVELOPING
    assert result.breakdown_developing
    assert not result.breakdown_confirmed
    assert result.opposes_early_long


def test_breakdown_accepted_is_confirmed_bearish_authority() -> None:
    result = assess_stabil_authority(
        _stabil("BREAKDOWN_ACCEPTED", relation="BELOW_NEAR")
    )

    assert result.state is StabilDecisionState.BREAKDOWN_CONFIRMED
    assert result.breakdown_confirmed
    assert result.opposes_early_long


def test_downside_continuation_is_strongest_bearish_state() -> None:
    result = assess_stabil_authority(
        _stabil(
            "DOWNSIDE_CONTINUATION",
            motion="FALLING",
            relation="BELOW_FAR",
        )
    )

    assert result.state is StabilDecisionState.BEARISH_CONTINUATION
    assert result.breakdown_confirmed
    assert result.opposes_early_long


def test_falling_support_while_price_holds_above_is_softening_not_recovery() -> None:
    result = assess_stabil_authority(
        _stabil(
            "HOLDING_ABOVE",
            motion="FALLING",
            relation="ABOVE_FAR",
        )
    )

    assert result.state is StabilDecisionState.BULLISH_SOFTENING
    assert not result.recovery_confirmed
    assert not result.breakdown_confirmed


def test_warming_up_stabil_never_creates_directional_authority() -> None:
    result = assess_stabil_authority(
        _stabil("RECOVERY_CONFIRMED", quality=ContextDataQuality.WARMING_UP)
    )

    assert result.state is StabilDecisionState.UNKNOWN
    assert not result.usable
    assert not result.recovery_confirmed


def test_unavailable_stabil_never_creates_directional_authority() -> None:
    result = assess_stabil_authority(None)

    assert result.state is StabilDecisionState.UNKNOWN
    assert result.data_quality is ContextDataQuality.UNAVAILABLE
    assert not result.usable
    assert not result.recovery_confirmed
    assert not result.breakdown_confirmed
