from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_history import (
    StructureHistoryBoundaryState,
    assess_structure_history,
)
from financial_dashboard.engines.market_structure_state import (
    BosMaturity,
    EVENT_BOS,
    EVENT_CHOCH,
)
from financial_dashboard.engines.models import Direction


def _event(
    uid: str,
    *,
    event_bar: int,
    event_type: str = EVENT_BOS,
    maturity: BosMaturity = BosMaturity.NOT_APPLICABLE,
    confirmation: StructureEventConfirmation = StructureEventConfirmation.CONFIRMED,
) -> MarketStructureEventRecord:
    confirmed_at = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(
        hours=4 * event_bar
    )
    return MarketStructureEventRecord(
        event_uid=f"ASELS:4h:{uid}",
        identity=event_bar,
        scope="EXTERNAL",
        event_type=event_type,
        direction=Direction.UP,
        candidate_bar=event_bar - 1,
        event_bar=event_bar,
        candidate_at=confirmed_at - pd.Timedelta(hours=4),
        confirmed_at=confirmed_at,
        broken_swing_identity=event_bar,
        broken_source_bar=max(0, event_bar - 4),
        broken_source_at=confirmed_at - pd.Timedelta(hours=16),
        broken_level=368.50,
        origin_swing_identity=event_bar + 1,
        origin_source_bar=max(0, event_bar - 3),
        origin_source_at=confirmed_at - pd.Timedelta(hours=12),
        origin_price=346.75,
        quality=90.0,
        evidence_text="FIXTURE",
        confirmation_status=confirmation,
        validity=StructureEventValidity.VALID,
        relevance=StructureEventRelevance.CURRENT,
        outcome=StructureEventOutcome.OBSERVED,
        symbol="ASELS",
        timeframe="4h",
        confirmation_high=395.0,
        confirmation_low=365.0,
        confirmation_close=394.25,
        bos_maturity=maturity,
    )


def _assess(
    events: tuple[MarketStructureEventRecord, ...],
    *,
    current: tuple[str, ...] = (),
):
    return assess_structure_history(
        symbol="ASELS",
        timeframe="4h",
        input_bar_count=80,
        input_start=pd.Timestamp("2025-12-01T00:00:00Z"),
        input_end=pd.Timestamp("2026-01-20T00:00:00Z"),
        events=events,
        current_progression_event_uids=current,
    )


def test_history_reports_absence_without_claiming_sufficient_warmup() -> None:
    diagnostic = _assess(())

    assert diagnostic.state is StructureHistoryBoundaryState.NO_EXTERNAL_STRUCTURE
    assert diagnostic.input_bar_count == 80
    assert diagnostic.first_external_event_uid is None
    assert diagnostic.bars_before_initial_structure is None
    assert diagnostic.reasons == ("NO_CONFIRMED_EXTERNAL_BOS_OR_CHOCH_IN_CACHE",)


def test_current_initial_structure_marks_left_boundary_active() -> None:
    initial = _event(
        "initial",
        event_bar=27,
        maturity=BosMaturity.INITIAL_STRUCTURE,
    )

    diagnostic = _assess((initial,), current=(initial.event_uid,))

    assert diagnostic.state is StructureHistoryBoundaryState.LEFT_BOUNDARY_ACTIVE
    assert diagnostic.first_external_event_type == EVENT_BOS
    assert diagnostic.first_external_event_maturity is BosMaturity.INITIAL_STRUCTURE
    assert diagnostic.bars_before_first_external_event == 27
    assert diagnostic.bars_before_initial_structure == 27
    assert diagnostic.current_progression_uses_initial_structure
    assert "PRE_CACHE_DIRECTIONAL_CONTEXT_NOT_OBSERVED" in diagnostic.reasons


def test_initial_structure_can_remain_historical_without_being_current() -> None:
    initial = _event(
        "initial",
        event_bar=27,
        maturity=BosMaturity.INITIAL_STRUCTURE,
    )

    diagnostic = _assess((initial,))

    assert (
        diagnostic.state
        is StructureHistoryBoundaryState.INITIAL_STRUCTURE_NOT_CURRENT
    )
    assert not diagnostic.current_progression_uses_initial_structure


def test_later_structure_reports_observed_post_initial_chronology() -> None:
    initial = _event(
        "initial",
        event_bar=27,
        maturity=BosMaturity.INITIAL_STRUCTURE,
    )
    transition = _event(
        "transition",
        event_bar=43,
        maturity=BosMaturity.TRANSITION_CONFIRMATION,
    )
    choch = _event("choch", event_bar=55, event_type=EVENT_CHOCH)

    diagnostic = _assess(
        (choch, transition, initial),
        current=(choch.event_uid,),
    )

    assert diagnostic.state is StructureHistoryBoundaryState.POST_INITIAL_PROGRESSION
    assert diagnostic.external_structure_event_count == 3
    assert diagnostic.choch_count == 1
    assert diagnostic.transition_confirmation_bos_count == 1
    assert diagnostic.continuation_bos_count == 0
    assert not diagnostic.current_progression_uses_initial_structure


def test_unconfirmed_candidates_do_not_establish_observed_structure() -> None:
    failed_candidate = _event(
        "candidate",
        event_bar=12,
        event_type=EVENT_CHOCH,
        confirmation=StructureEventConfirmation.CANDIDATE_FAILED,
    )

    diagnostic = _assess((failed_candidate,))

    assert diagnostic.state is StructureHistoryBoundaryState.NO_EXTERNAL_STRUCTURE
    assert diagnostic.external_structure_event_count == 0
