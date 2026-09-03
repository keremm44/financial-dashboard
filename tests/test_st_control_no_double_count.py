from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.pattern_behavior_projection import PatternBehaviorPhase
from financial_dashboard.decision.st_control import ShortTermControlState, assess_short_term_control
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


class _Projection:
    def __init__(self, row):
        self._row = row

    def for_timeframe(self, timeframe: str):
        if timeframe.strip().lower() != "30m":
            raise KeyError(timeframe)
        return self._row


def _ref(domain: ContextDomain, fact_type: str, timeframe: str, native_id: str) -> FactRef:
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol="ASELS",
        timeframe=timeframe,
        native_id=native_id,
        native_state="TEST",
        origin_time=10,
        confirmed_at=10,
        available_at=10,
        lineage_id=None,
        causal_family=(
            CausalFamily.REGIME
            if domain is ContextDomain.PATTERN
            else CausalFamily.STRUCTURAL_LEVEL
        ),
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def test_one_pattern_lineage_cannot_fill_acceptance_and_defense_as_two_confirmations() -> None:
    structural = StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=StructuralDirection.SHORT,
        thesis_state=ThesisState.TRANSITIONING,
        native_state="STATE_TRANSITION_UP",
        transition_target=StructuralDirection.LONG,
        data_quality=ContextDataQuality.VALID,
        authority_as_of=10,
        protected_high=None,
        protected_low=None,
        weak_high=None,
        weak_low=None,
        source_refs=(
            _ref(ContextDomain.MARKET_STRUCTURE, "EVENT_CHOCH", "1h", "CHOCH_UP"),
        ),
        reasons=("TEST_TRANSITION_UP",),
    )
    pattern_ref = _ref(ContextDomain.PATTERN, "PATTERN_BEHAVIOR", "30m", "ONE_PATTERN")
    pattern_row = SimpleNamespace(
        ref=pattern_ref,
        phase=PatternBehaviorPhase.RETEST_HELD,
        native_state="RETEST_BASARILI",
        classic_direction=1,
    )
    snapshot = SimpleNamespace(
        symbol="ASELS",
        as_of=10,
        structure=None,
        participation_behavior=None,
        pattern_behavior=_Projection(pattern_row),
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
        support_resistance=None,
    )

    assessment = assess_short_term_control(snapshot, structural=structural)

    assert assessment.control_state is ShortTermControlState.CONTROL_CONTESTED
