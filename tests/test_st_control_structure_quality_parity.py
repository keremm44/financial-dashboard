from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.st_control import (
    ControlEvidenceRole,
    ShortTermControlState,
    assess_short_term_control,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


class _Projection:
    def __init__(self, rows: dict[str, object]):
        self._rows = rows

    def for_timeframe(self, timeframe: str):
        normalized = timeframe.strip().lower()
        if normalized not in self._rows:
            raise KeyError(normalized)
        return self._rows[normalized]


def _ref(
    timeframe: str,
    native_id: str,
    *,
    quality: ContextDataQuality,
    confirmed_at: int = 10,
) -> FactRef:
    return FactRef(
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type="EVENT_BOS",
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id,
        native_state="VALID:CURRENT",
        origin_time=confirmed_at,
        confirmed_at=confirmed_at,
        available_at=confirmed_at,
        lineage_id=None,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=quality,
    )


def _event(
    timeframe: str,
    side: StructuralDirection,
    *,
    native_id: str,
    quality: ContextDataQuality,
    event_type: str = "EVENT_BOS",
    bos_maturity: str = "CONTINUATION",
):
    direction = 1 if side is StructuralDirection.LONG else -1
    ref = _ref(timeframe, native_id, quality=quality)
    return SimpleNamespace(
        ref=ref,
        scope="EXTERNAL",
        event_type=event_type,
        direction=direction,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="OBSERVED",
        bos_maturity=bos_maturity,
    )


def _row(
    timeframe: str,
    *,
    state: str,
    direction: int,
    quality: ContextDataQuality,
    events: tuple[object, ...],
):
    scope = SimpleNamespace(state=state, direction=direction)
    return SimpleNamespace(
        timeframe=timeframe,
        data_quality=quality,
        external=scope,
        internal=scope,
        events=events,
    )


def _snapshot(rows: dict[str, object]):
    return SimpleNamespace(
        symbol="ASELS",
        as_of=10,
        structure=_Projection(rows),
        participation_behavior=None,
        pattern_behavior=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        support_resistance=None,
    )


def _structural(
    side: StructuralDirection,
    *,
    transitioning: bool,
    source_refs: tuple[FactRef, ...],
) -> StructuralAssessment:
    transition_target = (
        StructuralDirection.LONG
        if side is StructuralDirection.SHORT and transitioning
        else StructuralDirection.SHORT
        if side is StructuralDirection.LONG and transitioning
        else None
    )
    return StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=side,
        thesis_state=ThesisState.TRANSITIONING if transitioning else ThesisState.INTACT,
        native_state=(
            "STATE_TRANSITION_UP"
            if side is StructuralDirection.SHORT and transitioning
            else "STATE_TRANSITION_DOWN"
            if side is StructuralDirection.LONG and transitioning
            else "STATE_BULLISH"
            if side is StructuralDirection.LONG
            else "STATE_BEARISH"
        ),
        transition_target=transition_target,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=source_refs,
        reasons=("TEST_CANONICAL_STRUCTURE",),
    )


def test_30m_data_limited_structure_can_supply_causal_migration_without_quality_promotion() -> None:
    migration_event = _event(
        "30m",
        StructuralDirection.LONG,
        native_id="MS:30m:CHOCH:1",
        quality=ContextDataQuality.DATA_LIMITED,
        event_type="EVENT_CHOCH",
        bos_maturity="NOT_APPLICABLE",
    )
    snapshot = _snapshot(
        {
            "30m": _row(
                "30m",
                state="STATE_TRANSITION_UP",
                direction=0,
                quality=ContextDataQuality.DATA_LIMITED,
                events=(migration_event,),
            )
        }
    )
    authority_ref = _ref(
        "1h",
        "MS:1h:CHOCH:1",
        quality=ContextDataQuality.VALID,
    )
    structural = _structural(
        StructuralDirection.SHORT,
        transitioning=True,
        source_refs=(authority_ref,),
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    migration = [
        item
        for item in assessment.evidence
        if item.role is ControlEvidenceRole.CONTROL_MIGRATION
    ]
    assert migration
    assert all(
        ref.data_quality is ContextDataQuality.DATA_LIMITED
        for item in migration
        for ref in item.source_refs
    )
    assert assessment.data_quality is ContextDataQuality.DATA_LIMITED
    assert assessment.control_state is ShortTermControlState.CONTROL_CONTESTED


def test_2h_data_limited_structure_remains_fail_closed_for_migration() -> None:
    migration_event = _event(
        "2h",
        StructuralDirection.LONG,
        native_id="MS:2h:CHOCH:1",
        quality=ContextDataQuality.DATA_LIMITED,
        event_type="EVENT_CHOCH",
        bos_maturity="NOT_APPLICABLE",
    )
    snapshot = _snapshot(
        {
            "2h": _row(
                "2h",
                state="STATE_TRANSITION_UP",
                direction=0,
                quality=ContextDataQuality.DATA_LIMITED,
                events=(migration_event,),
            )
        }
    )
    structural = _structural(
        StructuralDirection.SHORT,
        transitioning=True,
        source_refs=(
            _ref("1h", "MS:1h:CHOCH:2", quality=ContextDataQuality.VALID),
        ),
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert ControlEvidenceRole.CONTROL_MIGRATION not in {
        item.role for item in assessment.evidence
    }
    assert assessment.control_state is ShortTermControlState.CONTROL_WEAKENING


def test_raw_data_limited_transition_confirmation_uses_canonical_authority_ref_parity() -> None:
    raw_confirmation = _event(
        "1h",
        StructuralDirection.LONG,
        native_id="MS:1h:BOS:transition-confirmation",
        quality=ContextDataQuality.DATA_LIMITED,
        event_type="EVENT_BOS",
        bos_maturity="TRANSITION_CONFIRMATION",
    )
    snapshot = _snapshot(
        {
            "1h": _row(
                "1h",
                state="STATE_BULLISH",
                direction=1,
                quality=ContextDataQuality.DATA_LIMITED,
                events=(raw_confirmation,),
            )
        }
    )
    canonical_ref = replace(
        raw_confirmation.ref,
        data_quality=ContextDataQuality.VALID,
    )
    structural = _structural(
        StructuralDirection.LONG,
        transitioning=False,
        source_refs=(canonical_ref,),
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    confirmation = [
        item
        for item in assessment.evidence
        if item.role is ControlEvidenceRole.TRANSFER_CONFIRMATION
    ]
    assert confirmation
    assert all(
        ref.data_quality is ContextDataQuality.VALID
        for item in confirmation
        for ref in item.source_refs
    )
    assert assessment.control_state is ShortTermControlState.TRANSFER_ESTABLISHED


def test_transition_confirmation_without_canonical_ref_parity_fails_closed() -> None:
    raw_confirmation = _event(
        "1h",
        StructuralDirection.LONG,
        native_id="MS:1h:BOS:raw-only",
        quality=ContextDataQuality.DATA_LIMITED,
        event_type="EVENT_BOS",
        bos_maturity="TRANSITION_CONFIRMATION",
    )
    snapshot = _snapshot(
        {
            "1h": _row(
                "1h",
                state="STATE_BULLISH",
                direction=1,
                quality=ContextDataQuality.DATA_LIMITED,
                events=(raw_confirmation,),
            )
        }
    )
    structural = _structural(
        StructuralDirection.LONG,
        transitioning=False,
        source_refs=(
            _ref("1h", "MS:1h:BOS:different", quality=ContextDataQuality.VALID),
        ),
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert ControlEvidenceRole.TRANSFER_CONFIRMATION not in {
        item.role for item in assessment.evidence
    }
    assert assessment.control_state is ShortTermControlState.CONTROL_HELD
