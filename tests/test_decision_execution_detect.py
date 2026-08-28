from types import SimpleNamespace

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.decision.execution import ExecutionTriggerState
from financial_dashboard.decision.execution_detect import detect_30m_execution_events
from financial_dashboard.decision.structural import StructuralDirection


def _pattern(phase: str, direction: int = 1, *, native_state=None, quality=None):
    ref = None if quality is None else SimpleNamespace(data_quality=quality, is_available_at=lambda _as_of: True)
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


def _snapshot(as_of, *, phase=None, direction=1, bos=(), native_state=None, quality=None):
    return SimpleNamespace(
        as_of=as_of,
        pattern_behavior=(
            None
            if phase is None
            else _pattern(phase, direction, native_state=native_state, quality=quality)
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
    assert event.timeframe == "30m"
    assert event.observed_at == 2
    assert event.available_at == 2
    assert event.reason == "30M_PATTERN_BREAK_CONFIRMED"


def test_sticky_confirmed_on_first_bar_is_not_an_event():
    entry, exit_events = detect_30m_execution_events(
        (_snapshot(1, phase="BREAK_CONFIRMED", direction=1),)
    )
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
    assert exit_events[2].state is ExecutionTriggerState.CONFIRMED


def test_data_limited_unavailable_phase_uses_native_break_state():
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
        _snapshot(
            3,
            phase="UNAVAILABLE",
            direction=1,
            native_state="KIRILIM_TEYITLI",
            quality=ContextDataQuality.DATA_LIMITED,
        ),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {2}
    assert entry[2].reason == "30M_PATTERN_BREAK_CONFIRMED"
    assert exit_events == {}


def test_new_30m_bos_identity_emits_once():
    first = _snapshot(1, phase="FORMING", bos=())
    second = _snapshot(2, phase="FORMING", bos=(_bos("bos-a", 1),))
    third = _snapshot(3, phase="FORMING", bos=(_bos("bos-a", 1),))
    entry, exit_events = detect_30m_execution_events((first, second, third))
    assert set(entry) == {2}
    assert entry[2].reason == "30M_STRUCTURE_BOS_CONFIRMED"
    assert exit_events == {}


def test_ledger_event_bos_type_is_recognized():
    snapshots = (
        _snapshot(1, phase="FORMING"),
        _snapshot(2, phase="FORMING", bos=(_bos("bos-a", 1, event_type="EVENT_BOS"),)),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {2}
    assert exit_events == {}


def test_two_same_side_new_bos_ids_still_confirm_once():
    snapshots = (
        _snapshot(1, phase="FORMING"),
        _snapshot(2, phase="FORMING", bos=(_bos("bos-a"), _bos("bos-b"))),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert set(entry) == {2}
    assert exit_events == {}


def test_opposite_new_bos_ids_fail_closed():
    snapshots = (
        _snapshot(1, phase="FORMING"),
        _snapshot(2, phase="FORMING", bos=(_bos("bos-a", 1), _bos("bos-b", -1))),
    )
    entry, exit_events = detect_30m_execution_events(snapshots)
    assert entry == {}
    assert exit_events == {}
