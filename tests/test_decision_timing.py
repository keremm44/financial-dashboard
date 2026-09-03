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
from financial_dashboard.decision.structural import DecisionHorizon, HorizonRelation, StructuralDirection
from financial_dashboard.decision.timing import (
    SetupTriggerState,
    TimingEntryEffect,
    TimingState,
    assess_setup_trigger,
    assess_timing,
)


def _ref(domain=ContextDomain.PATTERN, quality=ContextDataQuality.VALID):
    return FactRef(
        domain,
        "TEST",
        "THYAO",
        "30m",
        f"{domain.value}:1",
        "TEST",
        1,
        1,
        1,
        f"{domain.value}:1",
        CausalFamily.IMPULSE,
        SourceFamily.PRICE_GEOMETRY,
        quality,
    )


def _reaction(state, *, confirmed=False, failed=False, developing=False):
    return ReactionAssessment(
        state=state,
        failure_present=failed,
        confirmation_present=confirmed,
        developing_present=developing,
        data_quality=(
            ContextDataQuality.UNAVAILABLE
            if state is ReactionState.UNKNOWN
            else ContextDataQuality.VALID
        ),
        reasons=(state.value,),
        source_refs=() if state is ReactionState.UNKNOWN else (_ref(ContextDomain.ORDER_BLOCK),),
    )


def _pattern(phase, *, direction=1, quality=ContextDataQuality.VALID):
    row = SimpleNamespace(
        ref=_ref(ContextDomain.PATTERN, quality),
        phase=phase,
        classic_direction=direction,
    )
    return SimpleNamespace(for_timeframe=lambda timeframe: row)


def test_confirmed_reaction_is_enough_for_setup_without_pattern_dependency():
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.CONFIRMED, confirmed=True),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is SetupTriggerState.CONFIRMED


def test_aligned_confirmed_pattern_can_mature_setup():
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.ABSENT),
        pattern=_pattern(PatternBehaviorPhase.BREAK_CONFIRMED, direction=1),
        timeframe="30m",
    )
    assert result.state is SetupTriggerState.CONFIRMED


def test_opposite_confirmed_pattern_is_explicit_adverse_setup_evidence():
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.ABSENT),
        pattern=_pattern(PatternBehaviorPhase.BREAK_CONFIRMED, direction=-1),
        timeframe="30m",
    )
    assert result.state is SetupTriggerState.ADVERSE
    assert "OPPOSING_PATTERN_CONFIRMED:BREAK_CONFIRMED" in result.reasons


def test_st_opposite_confirmed_pattern_defers_with_adverse_effect():
    result = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.LONG,
        HorizonRelation.ALIGNED,
        reaction=_reaction(ReactionState.ABSENT),
        pattern=_pattern(PatternBehaviorPhase.RETEST_HELD, direction=-1),
        timeframe="30m",
    )
    assert result.state is TimingState.EARLY
    assert result.entry_effect is TimingEntryEffect.ADVERSE
    assert result.waiting_for == ("NEW_SETUP_PATH",)


def test_developing_reaction_maps_to_developing_timing():
    result = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.LONG,
        HorizonRelation.ALIGNED,
        reaction=_reaction(ReactionState.DEVELOPING, developing=True),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is TimingState.DEVELOPING
    assert result.entry_effect is TimingEntryEffect.NEUTRAL
    assert "SETUP_TRIGGER_CONFIRMATION" in result.waiting_for


def test_lt_counter_reaction_remains_early_even_when_setup_fact_is_confirmed():
    result = assess_timing(
        DecisionHorizon.LONG_TERM,
        StructuralDirection.LONG,
        HorizonRelation.COUNTER_REACTION,
        reaction=_reaction(ReactionState.CONFIRMED, confirmed=True),
        pattern=None,
        timeframe="1h",
    )
    assert result.state is TimingState.EARLY


def test_lt_opposite_pattern_keeps_legacy_early_wait_contract():
    result = assess_timing(
        DecisionHorizon.LONG_TERM,
        StructuralDirection.LONG,
        HorizonRelation.ALIGNED,
        reaction=_reaction(ReactionState.ABSENT),
        pattern=_pattern(PatternBehaviorPhase.BREAK_CONFIRMED, direction=-1),
        timeframe="30m",
    )
    assert result.state is TimingState.EARLY
    assert result.waiting_for == ("SETUP_TRIGGER",)


def test_st_counter_reaction_can_be_ready_on_its_own_horizon():
    result = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.SHORT,
        HorizonRelation.COUNTER_REACTION,
        reaction=_reaction(ReactionState.CONFIRMED, confirmed=True),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is TimingState.READY
    assert result.entry_effect is TimingEntryEffect.SUPPORTIVE


def test_pattern_missing_is_not_unavailable_when_reaction_was_validly_observed_absent():
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.ABSENT),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is SetupTriggerState.ABSENT


def test_all_setup_evidence_missing_is_unavailable_not_absent():
    result = assess_setup_trigger(
        StructuralDirection.LONG,
        reaction=_reaction(ReactionState.UNKNOWN),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is SetupTriggerState.UNAVAILABLE


def test_st_unavailable_timing_is_unknown_not_adverse():
    result = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.LONG,
        HorizonRelation.ALIGNED,
        reaction=_reaction(ReactionState.UNKNOWN),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is TimingState.UNAVAILABLE
    assert result.entry_effect is TimingEntryEffect.UNKNOWN


def test_failed_setup_has_failed_entry_effect():
    result = assess_timing(
        DecisionHorizon.SHORT_TERM,
        StructuralDirection.LONG,
        HorizonRelation.ALIGNED,
        reaction=_reaction(ReactionState.FAILED, failed=True),
        pattern=None,
        timeframe="30m",
    )
    assert result.state is TimingState.FAILED
    assert result.entry_effect is TimingEntryEffect.FAILED


def test_extended_is_not_emitted_without_calibration():
    cases = (
        _reaction(ReactionState.ABSENT),
        _reaction(ReactionState.DEVELOPING, developing=True),
        _reaction(ReactionState.CONFIRMED, confirmed=True),
        _reaction(ReactionState.FAILED, failed=True),
    )
    states = {
        assess_timing(
            DecisionHorizon.SHORT_TERM,
            StructuralDirection.LONG,
            HorizonRelation.ALIGNED,
            reaction=item,
            pattern=None,
            timeframe="30m",
        ).state
        for item in cases
    }
    assert TimingState.EXTENDED not in states
