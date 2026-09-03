from __future__ import annotations

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
    def __init__(self, row):
        self.row = row

    def for_timeframe(self, timeframe: str):
        if timeframe.strip().lower() != "1h":
            raise KeyError(timeframe)
        return self.row


def _ref(
    *,
    fact_type: str,
    native_id: str,
    available_at: int,
) -> FactRef:
    return FactRef(
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type=fact_type,
        symbol="ASELS",
        timeframe="1h",
        native_id=native_id,
        native_state="VALID:CURRENT",
        origin_time=available_at,
        confirmed_at=available_at,
        available_at=available_at,
        lineage_id=None,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _event(
    *,
    event_type: str,
    native_id: str,
    available_at: int,
    bos_maturity: str = "NOT_APPLICABLE",
):
    ref = _ref(
        fact_type=event_type,
        native_id=native_id,
        available_at=available_at,
    )
    return SimpleNamespace(
        ref=ref,
        scope="EXTERNAL",
        event_type=event_type,
        direction=1,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="OBSERVED",
        bos_maturity=bos_maturity,
    )


def _snapshot(*, as_of: int, events: tuple[object, ...]):
    scope = SimpleNamespace(state="STATE_BULLISH", direction=1)
    row = SimpleNamespace(
        timeframe="1h",
        data_quality=ContextDataQuality.VALID,
        external=scope,
        internal=scope,
        events=events,
    )
    return SimpleNamespace(
        symbol="ASELS",
        as_of=as_of,
        structure=_Projection(row),
        participation_behavior=None,
        pattern_behavior=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        support_resistance=None,
    )


def _structural(*, source_refs: tuple[FactRef, ...], as_of: int) -> StructuralAssessment:
    return StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        native_state="STATE_BULLISH",
        transition_target=None,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=as_of,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=source_refs,
        reasons=("TEST_CANONICAL_LONG",),
    )


def test_fresh_canonical_transition_fail_emits_failed_transfer() -> None:
    failure = _event(
        event_type="EVENT_TRANSITION_FAIL",
        native_id="MS:FAIL:10",
        available_at=10,
    )
    snapshot = _snapshot(as_of=10, events=(failure,))
    structural = _structural(source_refs=(failure.ref,), as_of=10)

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.TRANSFER_FAILED
    assert ControlEvidenceRole.TRANSFER_INVALIDATION in {
        item.role for item in assessment.evidence
    }


def test_transition_fail_is_not_sticky_after_its_availability_snapshot() -> None:
    failure = _event(
        event_type="EVENT_TRANSITION_FAIL",
        native_id="MS:FAIL:10",
        available_at=10,
    )
    snapshot = _snapshot(as_of=11, events=(failure,))
    structural = _structural(source_refs=(failure.ref,), as_of=11)

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.CONTROL_HELD
    assert ControlEvidenceRole.TRANSFER_INVALIDATION not in {
        item.role for item in assessment.evidence
    }


def test_newer_transition_fail_supersedes_older_transition_confirmation() -> None:
    confirmation = _event(
        event_type="EVENT_BOS",
        native_id="MS:BOS:8",
        available_at=8,
        bos_maturity="TRANSITION_CONFIRMATION",
    )
    failure = _event(
        event_type="EVENT_TRANSITION_FAIL",
        native_id="MS:FAIL:10",
        available_at=10,
    )
    snapshot = _snapshot(as_of=10, events=(confirmation, failure))
    structural = _structural(
        source_refs=(confirmation.ref, failure.ref),
        as_of=10,
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.TRANSFER_FAILED
    roles = {item.role for item in assessment.evidence}
    assert ControlEvidenceRole.TRANSFER_INVALIDATION in roles
    assert ControlEvidenceRole.TRANSFER_CONFIRMATION not in roles
