from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_state import (
    BosMaturity,
    EVIDENCE_INITIAL_STRUCTURE_BREAK_CONFIRMED,
    EVENT_BOS,
    EVENT_CHOCH,
    EVENT_TRANSITION_FAIL,
)
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.mtf_story_models import (
    ContextAssessment,
    ContextState,
    MTFStoryResult,
    MTFStoryState,
    TriggerAssessment,
    TriggerState,
)
from financial_dashboard.engines.structure_location import build_zone_confluence
from financial_dashboard.engines.support_resistance_zones import (
    SupportResistanceZone,
    ZoneKind,
    ZoneLifecycle,
    ZoneSide,
)
from financial_dashboard.engines.three_domain_observer import (
    CausalStructureEventObservation,
    CombinedObservationState,
    LocationContextState,
    MTFPressureState,
    ObserverTensionCode,
    OpposingZoneConflictConfig,
    PressureChange,
    RecoveryStatus,
    StructureProgressionStage,
    ZoneRoleConflictKind,
    build_location_context,
    build_mtf_pressure,
    build_structure_progression,
    combine_three_domains,
    find_opposing_zone_conflicts,
)


def _context(state: ContextState, direction: Direction) -> ContextAssessment:
    return ContextAssessment(
        state=state,
        direction=direction,
        anchor_timeframe="4h",
        usable_timeframes=("1d", "4h", "2h"),
    )


def _trigger(state: TriggerState, direction: Direction) -> TriggerAssessment:
    return TriggerAssessment(
        state=state,
        direction=direction,
        anchor_timeframe="1h",
        usable_timeframes=("1h", "30m"),
    )


def _story(context: ContextAssessment, trigger: TriggerAssessment) -> MTFStoryResult:
    return MTFStoryResult(
        state=MTFStoryState.RANGE_MIXED,
        timestamp=pd.Timestamp("2026-01-03T00:00:00Z"),
        dominant_direction=trigger.direction,
        macro_direction=context.direction,
        context_state=context.state,
        trigger_state=trigger.state,
        quality=60.0,
        confidence=0.5,
    )


def _event(
    uid: str,
    *,
    timeframe: str,
    event_type: str,
    direction: Direction,
    scope: str = "EXTERNAL",
    event_bar: int = 1,
    validity: StructureEventValidity = StructureEventValidity.VALID,
    bos_maturity: BosMaturity | None = None,
    evidence_text: str = "DIRECT_CONFIRMATION",
) -> MarketStructureEventRecord:
    confirmed_at = pd.Timestamp("2026-01-01T10:00:00Z") + pd.Timedelta(hours=event_bar)
    resolved_bos_maturity = (
        BosMaturity.CONTINUATION
        if bos_maturity is None and event_type == EVENT_BOS
        else bos_maturity or BosMaturity.NOT_APPLICABLE
    )
    return MarketStructureEventRecord(
        event_uid=f"X:{timeframe}:{uid}",
        identity=event_bar,
        scope=scope,
        event_type=event_type,
        direction=direction,
        candidate_bar=event_bar - 1,
        event_bar=event_bar,
        candidate_at=confirmed_at - pd.Timedelta(hours=1),
        confirmed_at=confirmed_at,
        broken_swing_identity=10,
        broken_source_bar=0,
        broken_source_at=confirmed_at - pd.Timedelta(hours=2),
        broken_level=101.0,
        origin_swing_identity=11,
        origin_source_bar=0,
        origin_source_at=confirmed_at - pd.Timedelta(hours=2),
        origin_price=99.0,
        quality=75.0,
        evidence_text=evidence_text,
        confirmation_status=StructureEventConfirmation.CONFIRMED,
        validity=validity,
        relevance=StructureEventRelevance.CURRENT,
        outcome=StructureEventOutcome.OBSERVED,
        symbol="X",
        timeframe=timeframe,
        confirmation_high=102.0,
        confirmation_low=99.5,
        confirmation_close=101.5,
        bos_maturity=resolved_bos_maturity,
    )


def _observed(event: MarketStructureEventRecord, available_at: str):
    return CausalStructureEventObservation(
        event=event,
        available_at=pd.Timestamp(available_at),
    )


def _zone(
    uid: str,
    *,
    timeframe: str,
    side: ZoneSide,
    low: float,
    high: float,
    symbol: str = "X",
    atr: float = 2.0,
) -> SupportResistanceZone:
    timestamp = pd.Timestamp("2026-01-01T10:00:00Z")
    return SupportResistanceZone(
        zone_uid=f"{symbol}:{timeframe}:{uid}",
        source_range_identity=1,
        kind=ZoneKind.RANGE_BOUNDARY,
        side=side,
        low=low,
        high=high,
        center=(low + high) * 0.5,
        lifecycle=ZoneLifecycle.ACTIVE,
        range_state="RANGE_ACTIVE",
        quality=75.0,
        touches=3,
        boundary_stability=80.0,
        reference_atr=atr,
        origin_bar=0,
        created_bar=2,
        created_at=timestamp,
        last_updated_bar=5,
        last_updated_at=timestamp,
        last_transition_bar=5,
        last_transition_at=timestamp,
        symbol=symbol,
        timeframe=timeframe,
    )


def test_bearish_pressure_weakening_is_not_promoted_to_bullish_recovery() -> None:
    context = _context(ContextState.BEARISH_CONTEXT, Direction.DOWN)
    trigger = _trigger(TriggerState.BULLISH_TRIGGER, Direction.UP)

    pressure = build_mtf_pressure(context, trigger, _story(context, trigger), ())

    assert pressure.state is MTFPressureState.BEARISH_PRESSURE_WEAKENING
    assert pressure.change is PressureChange.WEAKENING
    assert pressure.anchor_direction is Direction.DOWN
    assert pressure.lower_timeframe_direction is Direction.UP
    assert pressure.is_weakening
    assert pressure.recovery_status is RecoveryStatus.LOWER_TIMEFRAME_REACTION_ONLY
    assert "PRESSURE:WEAKENING_IS_NOT_BULLISH_RECOVERY" in pressure.reasons


def test_context_transition_is_distinct_from_lower_timeframe_weakening() -> None:
    context = _context(ContextState.TRANSITION_CONTEXT, Direction.UP)
    trigger = _trigger(TriggerState.BULLISH_TRIGGER, Direction.UP)

    pressure = build_mtf_pressure(context, trigger, _story(context, trigger), ())

    assert pressure.state is MTFPressureState.TRANSITIONAL_PRESSURE
    assert pressure.change is PressureChange.TRANSITION
    assert pressure.recovery_status is RecoveryStatus.HIGHER_TIMEFRAME_RECOVERY_BUILDING
    assert not pressure.is_weakening


def test_structure_progression_requires_direct_external_timeframe_evidence() -> None:
    observations = (
        _observed(
            _event("M30_CHOCH", timeframe="30m", event_type=EVENT_CHOCH, direction=Direction.UP),
            "2026-01-01T11:00:00Z",
        ),
        _observed(
            _event(
                "H4_INTERNAL_BOS",
                timeframe="4h",
                event_type=EVENT_BOS,
                direction=Direction.UP,
                scope="INTERNAL",
                event_bar=2,
            ),
            "2026-01-01T12:00:00Z",
        ),
    )

    snapshot = build_structure_progression(
        observations,
        as_of=pd.Timestamp("2026-01-01T12:00:00Z"),
    )

    assert snapshot.upward.stage is StructureProgressionStage.M30_CHOCH
    assert snapshot.upward.rank == 1
    assert snapshot.upward.timeframe == "30m"
    assert snapshot.latest_external_for("4h") is None
    assert snapshot.latest_internal_for("4h").event_uid.endswith("H4_INTERNAL_BOS")


def test_bos_choch_progression_and_opposing_directions_remain_independent() -> None:
    observations = (
        _observed(
            _event("M30_BOS", timeframe="30m", event_type=EVENT_BOS, direction=Direction.UP),
            "2026-01-01T11:00:00Z",
        ),
        _observed(
            _event("H1_CHOCH", timeframe="1h", event_type=EVENT_CHOCH, direction=Direction.UP, event_bar=2),
            "2026-01-01T12:00:00Z",
        ),
        _observed(
            _event("H2_BOS", timeframe="2h", event_type=EVENT_BOS, direction=Direction.DOWN, event_bar=3),
            "2026-01-01T13:00:00Z",
        ),
    )

    snapshot = build_structure_progression(
        observations,
        as_of=pd.Timestamp("2026-01-01T13:00:00Z"),
    )

    assert snapshot.upward.stage is StructureProgressionStage.H1_CHOCH
    assert snapshot.upward.directly_confirmed_timeframes == ("1h", "30m")
    assert snapshot.downward.stage is StructureProgressionStage.H2_BOS
    assert snapshot.downward.directly_confirmed_timeframes == ("2h",)


def test_initial_h4_structure_is_not_promoted_to_mature_h4_bos() -> None:
    initial_h4 = replace(
        _event(
            "ASELS_INITIAL_H4",
            timeframe="4h",
            event_type=EVENT_BOS,
            direction=Direction.UP,
            bos_maturity=BosMaturity.INITIAL_STRUCTURE,
            evidence_text=EVIDENCE_INITIAL_STRUCTURE_BREAK_CONFIRMED,
        ),
        broken_level=368.50,
        origin_price=346.75,
        confirmation_close=394.25,
        quality=90.0,
    )

    initial_snapshot = build_structure_progression(
        (_observed(initial_h4, "2026-01-01T11:00:00Z"),),
        as_of=pd.Timestamp("2026-01-01T11:00:00Z"),
    )

    assert initial_snapshot.upward.stage is StructureProgressionStage.H4_INITIAL_STRUCTURE
    assert initial_snapshot.upward.stage is not StructureProgressionStage.H4_BOS
    assert initial_snapshot.upward.rank == 7
    assert initial_snapshot.upward.event_type == EVENT_BOS
    assert initial_snapshot.upward.bos_maturity is BosMaturity.INITIAL_STRUCTURE
    assert initial_snapshot.upward.directly_confirmed_timeframes == ("4h",)

    continuation_h4 = _event(
        "ASELS_CONTINUATION_H4",
        timeframe="4h",
        event_type=EVENT_BOS,
        direction=Direction.UP,
        event_bar=2,
        bos_maturity=BosMaturity.CONTINUATION,
    )
    continuation_snapshot = build_structure_progression(
        (
            _observed(initial_h4, "2026-01-01T11:00:00Z"),
            _observed(continuation_h4, "2026-01-01T12:00:00Z"),
        ),
        as_of=pd.Timestamp("2026-01-01T12:00:00Z"),
    )

    assert continuation_snapshot.upward.stage is StructureProgressionStage.H4_BOS
    assert continuation_snapshot.upward.rank == 8
    assert continuation_snapshot.upward.bos_maturity is BosMaturity.CONTINUATION


def test_future_event_cannot_change_an_earlier_progression_prefix() -> None:
    early = _observed(
        _event("EARLY", timeframe="30m", event_type=EVENT_BOS, direction=Direction.UP),
        "2026-01-01T11:00:00Z",
    )
    future = _observed(
        _event("FUTURE", timeframe="4h", event_type=EVENT_BOS, direction=Direction.UP, event_bar=5),
        "2026-01-02T00:00:00Z",
    )
    as_of = pd.Timestamp("2026-01-01T12:00:00Z")

    prefix = build_structure_progression((early,), as_of=as_of)
    full_input_same_prefix = build_structure_progression((early, future), as_of=as_of)
    later = build_structure_progression(
        (early, future),
        as_of=pd.Timestamp("2026-01-02T00:00:00Z"),
    )

    assert full_input_same_prefix == prefix
    assert prefix.upward.stage is StructureProgressionStage.M30_BOS
    assert later.upward.stage is StructureProgressionStage.H4_BOS


def test_future_failure_annotation_does_not_leak_into_earlier_as_of_progression() -> None:
    native_choch = _event(
        "CHOCH",
        timeframe="1h",
        event_type=EVENT_CHOCH,
        direction=Direction.UP,
    )
    failure = _event(
        "FAILURE",
        timeframe="1h",
        event_type=EVENT_TRANSITION_FAIL,
        direction=Direction.DOWN,
        event_bar=3,
    )
    final_annotated_choch = replace(
        native_choch,
        validity=StructureEventValidity.FAILED,
        relevance=StructureEventRelevance.HISTORICAL,
        outcome=StructureEventOutcome.FAILED,
        failed_by_event_uid=failure.event_uid,
    )
    observations = (
        _observed(final_annotated_choch, "2026-01-01T11:00:00Z"),
        _observed(failure, "2026-01-02T00:00:00Z"),
    )

    before_failure = build_structure_progression(
        observations,
        as_of=pd.Timestamp("2026-01-01T12:00:00Z"),
    )
    after_failure = build_structure_progression(
        observations,
        as_of=pd.Timestamp("2026-01-02T00:00:00Z"),
    )

    assert before_failure.upward.stage is StructureProgressionStage.H1_CHOCH
    assert before_failure.latest_external_for("1h").validity is StructureEventValidity.VALID
    assert after_failure.upward.stage is StructureProgressionStage.NONE
    assert after_failure.latest_external_for("1h").validity is StructureEventValidity.FAILED


def test_latest_failed_event_does_not_resurrect_superseded_direction() -> None:
    old_up = _observed(
        _event("OLD_UP", timeframe="30m", event_type=EVENT_BOS, direction=Direction.UP),
        "2026-01-01T11:00:00Z",
    )
    failed_down = _observed(
        _event(
            "FAILED_DOWN",
            timeframe="30m",
            event_type=EVENT_CHOCH,
            direction=Direction.DOWN,
            event_bar=2,
            validity=StructureEventValidity.FAILED,
        ),
        "2026-01-01T12:00:00Z",
    )

    snapshot = build_structure_progression(
        (old_up, failed_down),
        as_of=pd.Timestamp("2026-01-01T12:00:00Z"),
    )

    assert snapshot.latest_external_for("30m").event_uid.endswith("FAILED_DOWN")
    assert snapshot.upward.stage is StructureProgressionStage.NONE
    assert snapshot.downward.stage is StructureProgressionStage.NONE


def test_opposing_overlap_is_location_conflict_not_positive_confluence() -> None:
    zones = (
        _zone("D_SUPPORT", timeframe="1d", side=ZoneSide.SUPPORT, low=99.0, high=101.0),
        _zone("H4_SUPPORT", timeframe="4h", side=ZoneSide.SUPPORT, low=99.5, high=101.5),
        _zone("H1_RESISTANCE", timeframe="1h", side=ZoneSide.RESISTANCE, low=100.5, high=102.0),
    )
    confluence = build_zone_confluence(zones)

    location = build_location_context(zones, confluence, ())

    assert len(confluence) == 1
    assert confluence[0].side is ZoneSide.SUPPORT
    assert location.state is LocationContextState.OPPOSING_ZONE_CONFLICT
    assert len(location.opposing_conflicts) == 2
    assert all(
        conflict.kind is ZoneRoleConflictKind.OVERLAP
        for conflict in location.opposing_conflicts
    )
    assert {
        (conflict.overlap_low, conflict.overlap_high)
        for conflict in location.opposing_conflicts
    } == {(100.5, 101.0), (100.5, 101.5)}


def test_opposing_zone_conflicts_are_symbol_safe_and_config_validated() -> None:
    zones = (
        _zone("SUPPORT", timeframe="1d", side=ZoneSide.SUPPORT, low=99.0, high=100.0),
        _zone("OTHER", timeframe="4h", side=ZoneSide.RESISTANCE, low=99.5, high=100.5, symbol="Y"),
    )

    assert find_opposing_zone_conflicts(zones) == ()
    with pytest.raises(ValueError, match="max_gap_atr"):
        OpposingZoneConflictConfig(max_gap_atr=-0.01)
    with pytest.raises(ValueError, match="max_gap_atr"):
        OpposingZoneConflictConfig(max_gap_atr=float("nan"))
    with pytest.raises(ValueError, match="positive ATR"):
        find_opposing_zone_conflicts(
            (
                replace(zones[0], reference_atr=0.0),
                replace(zones[1], symbol="X"),
            )
        )


def test_domain_builders_reject_cross_symbol_mixing() -> None:
    x_event = _event(
        "X_EVENT",
        timeframe="30m",
        event_type=EVENT_BOS,
        direction=Direction.UP,
    )
    y_event = replace(x_event, event_uid="Y:30m:Y_EVENT", symbol="Y")
    observations = (
        _observed(x_event, "2026-01-01T11:00:00Z"),
        _observed(y_event, "2026-01-01T11:00:00Z"),
    )
    zones = (
        _zone("X_ZONE", timeframe="1d", side=ZoneSide.SUPPORT, low=99.0, high=100.0),
        _zone("Y_ZONE", timeframe="4h", side=ZoneSide.RESISTANCE, low=99.5, high=100.5, symbol="Y"),
    )

    with pytest.raises(ValueError, match="cannot mix symbols"):
        build_structure_progression(
            observations,
            as_of=pd.Timestamp("2026-01-01T11:00:00Z"),
        )
    with pytest.raises(ValueError, match="cannot mix symbols"):
        build_location_context(zones, (), ())


def test_combined_observer_reports_tension_without_cross_domain_gating() -> None:
    context = _context(ContextState.BEARISH_CONTEXT, Direction.DOWN)
    trigger = _trigger(TriggerState.BULLISH_TRIGGER, Direction.UP)
    pressure = build_mtf_pressure(context, trigger, _story(context, trigger), ())
    structure = build_structure_progression(
        (
            _observed(
                _event("H1_BOS", timeframe="1h", event_type=EVENT_BOS, direction=Direction.UP),
                "2026-01-01T11:00:00Z",
            ),
        ),
        as_of=pd.Timestamp("2026-01-01T11:00:00Z"),
    )
    location = build_location_context((), (), ())

    observation = combine_three_domains(
        symbol="X",
        as_of=pd.Timestamp("2026-01-01T11:00:00Z"),
        pressure=pressure,
        structure=structure,
        location=location,
    )

    assert observation.contract_version == 2
    assert observation.state is CombinedObservationState.CROSS_DOMAIN_TENSION
    assert ObserverTensionCode.LOWER_TF_OPPOSES_GENERAL_PRESSURE in observation.tensions
    assert observation.pressure is pressure
    assert observation.structure is structure
    assert observation.location is location
    assert not hasattr(observation, "action")
