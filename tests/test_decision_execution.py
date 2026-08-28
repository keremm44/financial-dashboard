import pytest

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.execution import (
    ExecutionTriggerEvent,
    ExecutionTriggerState,
    assess_execution_trigger,
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


def _event(*, observed_at=10, available_at=10, side=StructuralDirection.LONG):
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="30m",
        observed_at=observed_at,
        available_at=available_at,
        reason="FRESH_30M_EXECUTION_EVENT",
        source_refs=(_ref(available_at=available_at),),
    )


def test_valid_channel_without_fresh_event_is_absent():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality=ContextDataQuality.VALID,
    )
    assert result.state is ExecutionTriggerState.ABSENT


def test_data_limited_channel_without_event_is_absent_not_unavailable():
    result = assess_execution_trigger(
        StructuralDirection.LONG,
        as_of=10,
        timeframe="30m",
        data_quality=ContextDataQuality.DATA_LIMITED,
    )
    assert result.state is ExecutionTriggerState.ABSENT


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


def test_stale_event_cannot_be_reused_on_later_bar():
    with pytest.raises(ValueError, match="fresh"):
        assess_execution_trigger(
            StructuralDirection.LONG,
            as_of=11,
            timeframe="30m",
            data_quality=ContextDataQuality.VALID,
            event=_event(observed_at=10),
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
