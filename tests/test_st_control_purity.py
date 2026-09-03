from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.st_control import assess_short_term_control
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


def _structure_ref() -> FactRef:
    return FactRef(
        domain=ContextDomain.MARKET_STRUCTURE,
        fact_type="EVENT_CHOCH",
        symbol="ASELS",
        timeframe="1h",
        native_id="ST_CONTROL_PURITY_CHOCH",
        native_state="VALID:CURRENT",
        origin_time=10,
        confirmed_at=10,
        available_at=10,
        lineage_id=None,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _structural() -> StructuralAssessment:
    return StructuralAssessment(
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
        source_refs=(_structure_ref(),),
        reasons=("TEST_TRANSITION",),
    )


def _snapshot(**extra):
    values = {
        "symbol": "ASELS",
        "as_of": 10,
        "structure": None,
        "participation_behavior": None,
        "pattern_behavior": None,
        "order_block_behavior": None,
        "fvg_engulfing_lifecycle": None,
        "support_resistance": None,
    }
    values.update(extra)
    return SimpleNamespace(**values)


def test_same_frozen_snapshot_produces_identical_control_assessment() -> None:
    snapshot = _snapshot()
    structural = _structural()

    first = assess_short_term_control(snapshot, structural=structural)
    second = assess_short_term_control(snapshot, structural=structural)

    assert first == second


def test_policy_layer_objects_do_not_affect_shadow_control() -> None:
    structural = _structural()
    baseline = assess_short_term_control(_snapshot(), structural=structural)
    noisy = assess_short_term_control(
        _snapshot(
            timing=object(),
            opportunity=object(),
            execution=object(),
            context=object(),
            permission=object(),
            eligibility=object(),
            qualification=object(),
        ),
        structural=structural,
    )

    assert noisy == baseline
