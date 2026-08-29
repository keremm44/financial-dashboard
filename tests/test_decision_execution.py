import pytest

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.execution import (
    ExecutionEventKind,
    ExecutionTriggerEvent,
    ExecutionTriggerState,
    assess_execution_trigger,
    execution_event_kind,
    is_entry_execution_click,
    is_exit_execution_click,
)
from financial_dashboard.decision.structural import StructuralDirection


def _ref(*, available_at=10):
    return FactRef(
        ContextDomain.PATTERN,
        "EXECUTION_TEST",
        "THYAO",
        "30m",
        "EXEC:1",
        "CONFIRMED",
        10,
        10,
        available_at,
        "EXEC:1",
        CausalFamily.IMPULSE,
        SourceFamily.PRICE_GEOMETRY,
        ContextDataQuality.VALID,
    )


def _event(
    *,
    observed_at=10,
    available_at=10,
    side=StructuralDirection.LONG,
    reason="FRESH_30M_EXECUTION_EVENT",
    kind=ExecutionEventKind.LEGACY,
):
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="30m",
        observed_at=observed_at,
        available_at=available_at,
        reason=reason,
        source_refs=(_ref(available_at=available_at),),
        kind=kind,
    )


def test_valid_channel_without_fresh_event_is_absent():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality=ContextDataQuality.VALID,
    )
    assert result.state is ExecutionTriggerState.ABSENT


def test_data_limited_execution_channel_fails_closed():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality=ContextDataQuality.DATA_LIMITED,
    )
    assert result.state is ExecutionTriggerState.UNAVAILABLE


def test_missing_trigger_data_is_unavailable_not_absent():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality=ContextDataQuality.UNAVAILABLE,
    )
    assert result.state is ExecutionTriggerState.UNAVAILABLE


def test_fresh_confirmed_event_is_accepted():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality=ContextDataQuality.VALID,
        event=_event(),
    )
    assert result.state is ExecutionTriggerState.CONFIRMED


def test_older_native_event_can_be_consumed_on_later_decision_bar():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=11,
        timeframe="30m",
        data_quality=ContextDataQuality.VALID,
        event=_event(observed_at=10, available_at=10),
    )
    assert result.state is ExecutionTriggerState.CONFIRMED


def test_future_observed_event_is_rejected():
    with pytest.raises(ValueError, match="future-observed"):
        assess_execution_trigger(
            StructuralDirection.LONG,
            as_of=10,
            timeframe="30m",
            data_quality=ContextDataQuality.VALID,
            event=_event(observed_at=11, available_at=10),
        )


def test_future_unavailable_event_is_rejected():
    with pytest.raises(ValueError, match="future-unavailable"):
        assess_execution_trigger(
            StructuralDirection.LONG,
            as_of=10,
            timeframe="30m",
            data_quality=ContextDataQuality.VALID,
            event=_event(observed_at=10, available_at=11),
        )


def test_opposite_side_event_is_rejected():
    with pytest.raises(ValueError, match="side"):
        assess_execution_trigger(
            StructuralDirection.LONG,
            as_of=10,
            timeframe="30m",
            data_quality=ContextDataQuality.VALID,
            event=_event(side=StructuralDirection.SHORT),
        )


def test_structure_bos_is_never_entry_or_exit_click():
    event = _event(kind=ExecutionEventKind.STRUCTURE_BOS, reason="30M_STRUCTURE_BOS_CONFIRMED")
    assert execution_event_kind(event) is ExecutionEventKind.STRUCTURE_BOS
    assert not is_entry_execution_click(event)
    assert not is_exit_execution_click(event)


def test_legacy_bos_reason_is_still_classified_as_structure_bos():
    event = _event(reason="30M_STRUCTURE_BOS_CONFIRMED")
    assert execution_event_kind(event) is ExecutionEventKind.STRUCTURE_BOS
    assert not is_entry_execution_click(event)


def test_pattern_and_reaction_events_are_executable_clicks():
    pattern = _event(kind=ExecutionEventKind.PATTERN_CONFIRMATION)
    reaction = _event(kind=ExecutionEventKind.REACTION_CONFIRMATION, reason="30M_REACTION_CONFIRMED")
    assert is_entry_execution_click(pattern)
    assert is_exit_execution_click(pattern)
    assert is_entry_execution_click(reaction)
    assert is_exit_execution_click(reaction)
