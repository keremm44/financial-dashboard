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
from financial_dashboard.decision.reaction import ReactionAssessment, ReactionState
from financial_dashboard.decision.structural import StructuralDirection
from financial_dashboard.decision.timing import SetupTriggerState, assess_setup_trigger
from financial_dashboard.engines.pattern_compression_core import ST_BREAK_CONFIRMED, ST_NONE


def _ref() -> FactRef:
    return FactRef(
        domain=ContextDomain.PATTERN,
        fact_type="PATTERN_BEHAVIOR",
        symbol="ASELS",
        timeframe="1h",
        native_id="pattern:1h:1",
        native_state="test",
        origin_time=1,
        confirmed_at=1,
        available_at=1,
        lineage_id=None,
        causal_family=CausalFamily.IMPULSE,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.DATA_LIMITED,
    )


def _pattern(native_state: str, *, direction: int):
    row = SimpleNamespace(
        ref=_ref(),
        phase=PatternBehaviorPhase.UNAVAILABLE,
        native_state=native_state,
        classic_direction=direction,
    )
    return SimpleNamespace(for_timeframe=lambda timeframe: row)


def _unknown_reaction() -> ReactionAssessment:
    return ReactionAssessment(
        state=ReactionState.UNKNOWN,
        failure_present=False,
        confirmation_present=False,
        developing_present=False,
        data_quality=ContextDataQuality.UNAVAILABLE,
        reasons=("REACTION_EVIDENCE_UNAVAILABLE",),
        source_refs=(),
    )


def test_data_limited_price_pattern_preserves_observed_absence_for_decision() -> None:
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_unknown_reaction(),
        pattern=_pattern(ST_NONE, direction=0),
        timeframe="1h",
    )

    assert result.state is SetupTriggerState.ABSENT
    assert "NO_PATTERN_OBSERVED" in result.reasons
    assert result.source_refs[0].data_quality is ContextDataQuality.VALID


def test_data_limited_price_pattern_can_preserve_native_confirmation_for_decision() -> None:
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_unknown_reaction(),
        pattern=_pattern(ST_BREAK_CONFIRMED, direction=1),
        timeframe="1h",
    )

    assert result.state is SetupTriggerState.CONFIRMED
    assert "PATTERN_SETUP_CONFIRMED:BREAK_CONFIRMED" in result.reasons
