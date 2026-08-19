from financial_dashboard.engines.market_structure import SCOPE_EXTERNAL, SIDE_HIGH, SIDE_LOW, SWING_CANDIDATE, SWING_CONFIRMED, SwingPoint
from financial_dashboard.engines.market_structure_runtime import MarketStructureRuntime
from financial_dashboard.engines.market_structure_state import (
    EVENT_BOS,
    EVENT_CHOCH,
    ROLE_PROTECTED_HIGH,
    ROLE_PROTECTED_LOW,
    ROLE_WEAK_HIGH,
    STATE_BEARISH,
    STATE_BULLISH,
    STATE_TRANSITION_UP,
)


def _s(identity: int, side: str, source_bar: int, price: float, *, role: str = "", state: str = SWING_CONFIRMED) -> SwingPoint:
    return SwingPoint(
        valid=True,
        identity=identity,
        scope=SCOPE_EXTERNAL,
        side=side,
        state=state,
        source_bar=source_bar,
        confirm_bar=source_bar + 2,
        price=price,
        atr_at_source=1.0,
        prominence_atr=1.0,
        distance_atr=1.0,
        quality=70.0,
        finalized=state == SWING_CONFIRMED,
        structural_role=role,
    )


def test_neutral_bos_establishes_bullish_structure_same_bar() -> None:
    runtime = MarketStructureRuntime()
    high = _s(1, SIDE_HIGH, 5, 100)
    low = _s(2, SIDE_LOW, 10, 95)
    swings = [high, low]

    events = runtime.process_scope(
        scope=SCOPE_EXTERNAL,
        swings=swings,
        high_candidate=SwingPoint(),
        low_candidate=SwingPoint(),
        bar_index=20,
        open_=100.05,
        high=101.0,
        low=100.0,
        close=100.85,
        safe_atr=1.0,
    )

    assert any(event.event_type == EVENT_BOS for event in events)
    assert runtime.external.context.state == STATE_BULLISH
    assert runtime.external.context.direction == 1
    assert runtime.external.context.protected_low_identity == 2


def test_bearish_protected_high_break_enters_transition_up_not_full_reversal() -> None:
    runtime = MarketStructureRuntime()
    protected_high = _s(1, SIDE_HIGH, 5, 100, role=ROLE_PROTECTED_HIGH)
    origin_low = _s(2, SIDE_LOW, 10, 95)
    swings = [protected_high, origin_low]
    runtime.external.context.state = STATE_BEARISH
    runtime.external.context.direction = -1
    runtime.external.context.protected_high_identity = 1
    runtime.external.context.last_confirmed_high_identity = 1
    runtime.external.context.last_confirmed_low_identity = 2

    events = runtime.process_scope(
        scope=SCOPE_EXTERNAL,
        swings=swings,
        high_candidate=SwingPoint(),
        low_candidate=SwingPoint(),
        bar_index=20,
        open_=100.0,
        high=101.0,
        low=99.9,
        close=100.9,
        safe_atr=1.0,
    )

    assert any(event.event_type == EVENT_CHOCH for event in events)
    assert runtime.external.context.state == STATE_TRANSITION_UP
    assert runtime.external.context.direction == 0
    assert runtime.external.context.protected_low_identity == 2


def test_transition_bos_confirms_new_direction_and_exports_levels() -> None:
    runtime = MarketStructureRuntime()
    old_low = _s(1, SIDE_LOW, 5, 94, role=ROLE_PROTECTED_LOW)
    weak_high = _s(2, SIDE_HIGH, 10, 100, role=ROLE_WEAK_HIGH)
    new_low = _s(3, SIDE_LOW, 15, 96)
    swings = [old_low, weak_high, new_low]
    runtime.external.context.state = STATE_TRANSITION_UP
    runtime.external.context.direction = 0
    runtime.external.context.protected_low_identity = 1
    runtime.external.context.weak_high_identity = 2
    runtime.external.context.last_confirmed_high_identity = 2
    runtime.external.context.last_confirmed_low_identity = 3

    events = runtime.process_scope(
        scope=SCOPE_EXTERNAL,
        swings=swings,
        high_candidate=SwingPoint(),
        low_candidate=SwingPoint(),
        bar_index=20,
        open_=100.05,
        high=101.0,
        low=100.0,
        close=100.85,
        safe_atr=1.0,
    )

    assert any(event.event_type == EVENT_BOS for event in events)
    assert runtime.external.context.state == STATE_BULLISH
    assert runtime.external.context.protected_low_identity == 3
    exported = runtime.export(swings, [], bar_index=20)
    assert exported.external_state == 2.0
    assert exported.external_protected_low == 96
    assert exported.handshake == 314159.0


def test_active_break_candidate_locks_matching_swing_candidate_identity() -> None:
    runtime = MarketStructureRuntime()
    provisional_high = _s(5, SIDE_HIGH, 10, 100, state=SWING_CANDIDATE)
    origin_low = _s(6, SIDE_LOW, 12, 95, state=SWING_CANDIDATE)
    runtime.external.next_candidate_id = 7
    runtime.external.candidate.valid = True
    runtime.external.candidate.scope = SCOPE_EXTERNAL
    runtime.external.candidate.broken_swing_identity = 5
    runtime.external.candidate.origin_swing_identity = 6

    assert runtime.locks_candidate(SCOPE_EXTERNAL, provisional_high)
    assert runtime.locks_candidate(SCOPE_EXTERNAL, origin_low)
