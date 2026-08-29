from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.execution import ExecutionEventKind, ExecutionTriggerState
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.structural import StructuralDirection


def _pattern(
    phase: str,
    direction: int = 1,
    *,
    native_state=None,
    quality=None,
    confirmed_at=None,
    available_at=None,
):
    ref = None
    if quality is not None or confirmed_at is not None or available_at is not None:
        ref = SimpleNamespace(
            data_quality=ContextDataQuality.VALID if quality is None else quality,
            confirmed_at=confirmed_at,
            available_at=available_at,
            origin_time=confirmed_at,
            is_available_at=lambda as_of: available_at is None or available_at <= as_of,
        )
    return SimpleNamespace(
        for_timeframe=lambda timeframe: SimpleNamespace(
            phase=phase,
            classic_direction=direction,
            native_state=native_state,
            ref=ref,
        )
        if timeframe == "30m"
        else (_ for _ in ()).throw(KeyError(timeframe))
    )


def _structure(*events):
    return SimpleNamespace(
        for_timeframe=lambda timeframe: SimpleNamespace(events=events)
        if timeframe == "30m"
        else (_ for _ in ()).throw(KeyError(timeframe))
    )


def _bos(native_id: str, direction: int = 1, event_type: str = "BOS"):
    return SimpleNamespace(
        event_type=event_type,
        confirmation_status="CONFIRMED",
        direction=direction,
        ref=SimpleNamespace(native_id=native_id),
    )


def _snapshot(
    as_of,
    *,
    phase=None,
    direction=1,
    bos=(),
    native_state=None,
    quality=None,
    confirmed_at=None,
    available_at=None,
):
    return SimpleNamespace(
        as_of=as_of,
        pattern_behavior=(
            None
            if phase is None
            else _pattern(
                phase,
                direction,
                native_state=native_state,
                quality=quality,
                confirmed_at=confirmed_at,
                available_at=available_at,
            )
        ),
        structure=_structure(*bos),
    )


def test_forming_to_break_confirmed_emits_long_entry_once():
    snapshots = (
        _snapshot(1, phase="FORMING", direction=1),
        _snapshot(2, phase="BREAK_CONFIRMED", direction=1),
        _snapshot(3, phase="BREAK_CONFIRMED", direction=1),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {2}
    assert exit_events == {}
    event = entry[2]
    assert event.state is ExecutionTriggerState.CONFIRMED
    assert event.side is StructuralDirection.LONG
    assert event.kind is ExecutionEventKind.PATTERN_CONFIRMATION
    assert event.timeframe == "30m"
    assert event.observed_at == 2
    assert event.available_at == 2
    assert event.reason == "30M_PATTERN_BREAK_CONFIRMED"


def test_native_1030_pattern_event_is_owned_by_1100_decision_window():
    snapshots = (
        _snapshot(10, phase="FORMING", direction=1),
        _snapshot(
            11,
            phase="BREAK_CONFIRMED",
            direction=1,
            confirmed_at=10.5,
            available_at=10.5,
        ),
        _snapshot(12, phase="BREAK_CONFIRMED", direction=1),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {11}
    assert exit_events == {}
    assert entry[11].observed_at == 10.5
    assert entry[11].available_at == 10.5


def test_sticky_confirmed_on_first_bar_is_not_an_event():
    entry, exit_events = detect_30m_execution_events((_snapshot(1, phase="BREAK_CONFIRMED", direction=1),))
    assert entry == {}
    assert exit_events == {}


def test_break_confirmed_to_failed_does_not_invent_buy_or_sell():
    snapshots = (
        _snapshot(1, phase="FORMING", direction=1),
        _snapshot(2, phase="BREAK_CONFIRMED", direction=1),
        _snapshot(3, phase="BREAK_FAILED", direction=1),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {2}
    assert exit_events == {}


def test_short_confirm_feeds_exit_map_not_entry():
    snapshots = (
        _snapshot(1, phase="BREAK_ATTEMPT", direction=-1),
        _snapshot(2, phase="BREAK_CONFIRMED", direction=-1),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert entry == {}
    assert set(exit_events) == {2}
    assert exit_events[2].side is StructuralDirection.SHORT
    assert exit_events[2].kind is ExecutionEventKind.PATTERN_CONFIRMATION


def test_data_limited_native_pattern_transition_is_detected_but_execution_quality_is_validated_later():
    snapshots = (
        _snapshot(
            1,
            phase="UNAVAILABLE",
            direction=1,
            native_state="KIRILIM_DENEMESI",
            quality=ContextDataQuality.DATA_LIMITED,
        ),
        _snapshot(
            2,
            phase="UNAVAILABLE",
            direction=1,
            native_state="KIRILIM_TEYITLI",
            quality=ContextDataQuality.DATA_LIMITED,
        ),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {2}
    assert entry[2].kind is ExecutionEventKind.PATTERN_CONFIRMATION
    assert exit_events == {}


def test_new_30m_bos_is_diagnostic_not_entry_click():
    first = _snapshot(1, phase="FORMING", bos=())
    second = _snapshot(2, phase="FORMING", bos=(_bos("bos-a", 1),))
    third = _snapshot(3, phase="FORMING", bos=(_bos("bos-a", 1),))
    entry, exit_events = detect_30m_execution_events((first, second, third))
    assert entry == {}
    assert exit_events == {}


def test_ledger_event_bos_type_is_diagnostic_not_entry_click():
    snapshots = (
        _snapshot(1, phase="FORMING"),
        _snapshot(2, phase="FORMING", bos=(_bos("bos-a", 1, event_type="EVENT_BOS"),)),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert entry == {}
    assert exit_events == {}


def test_two_same_side_new_bos_ids_do_not_create_execution_click():
    snapshots = (
        _snapshot(1, phase="FORMING"),
        _snapshot(2, phase="FORMING", bos=(_bos("bos-a"), _bos("bos-b"))),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert entry == {}
    assert exit_events == {}


def test_opposite_new_bos_ids_fail_closed():
    snapshots = (
        _snapshot(1, phase="FORMING"),
        _snapshot(2, phase="FORMING", bos=(_bos("bos-a", 1), _bos("bos-b", -1))),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert entry == {}
    assert exit_events == {}
