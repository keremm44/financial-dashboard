from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.st_control import ShortTermControlState, assess_short_term_control
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


class _StructureProjection:
    def __init__(self, row):
        self._row = row

    def for_timeframe(self, timeframe: str):
        if timeframe.strip().lower() != "1h":
            raise KeyError(timeframe)
        return self._row


def _event(native_id: str, *, confirmed_at: int, maturity: str):
    ref = FactRef(
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type="EVENT_BOS",
        symbol="ASELS",
        timeframe="1h",
        native_id=native_id,
        native_state="VALID:CURRENT",
        origin_time=confirmed_at,
        confirmed_at=confirmed_at,
        available_at=confirmed_at,
        lineage_id=None,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )
    return SimpleNamespace(
        ref=ref,
        scope="EXTERNAL",
        event_type="EVENT_BOS",
        direction=1,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="OBSERVED",
        bos_maturity=maturity,
    )


def _structural() -> StructuralAssessment:
    return StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=StructuralDirection.LONG,
        thesis_state=ThesisState.INTACT,
        native_state="STATE_BULLISH",
        transition_target=None,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(),
        reasons=("TEST_BULLISH",),
    )


def test_older_transition_confirmation_does_not_stick_after_newer_continuation_bos() -> None:
    row = SimpleNamespace(
        timeframe="1h",
        data_quality=ContextDataQuality.VALID,
        external=SimpleNamespace(state="STATE_BULLISH", direction=1),
        internal=None,
        events=(
            _event("OLD_TRANSITION_CONFIRMATION", confirmed_at=8, maturity="TRANSITION_CONFIRMATION"),
            _event("NEW_CONTINUATION", confirmed_at=10, maturity="CONTINUATION"),
        ),
    )
    snapshot = SimpleNamespace(
        symbol="ASELS",
        as_of=10,
        structure=_StructureProjection(row),
        participation_behavior=None,
        pattern_behavior=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        support_resistance=None,
    )

    assessment = assess_short_term_control(snapshot, structural=_structural())

    assert assessment.control_state is ShortTermControlState.CONTROL_HELD
