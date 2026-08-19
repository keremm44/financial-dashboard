from financial_dashboard.engines.market_structure import SCOPE_EXTERNAL, SIDE_HIGH, SIDE_LOW, SWING_CONFIRMED, SwingPoint
from financial_dashboard.engines.market_structure_state import (
    EVENT_BOS,
    EVENT_CHOCH,
    EVENT_TRANSITION_FAIL,
    ROLE_PROTECTED_HIGH,
    ROLE_PROTECTED_LOW,
    ROLE_WEAK_HIGH,
    STATE_BEARISH,
    STATE_BULLISH,
    STATE_TRANSITION_DOWN,
    STATE_TRANSITION_UP,
    BreakCandidate,
    BreakConfig,
    StructureContext,
    break_final_quality,
    evaluate_break_candidate,
    export_state_code,
    finalize_confirmed_break,
    is_sweep,
    normalize_directional_roles,
)


def _s(identity: int, side: str, source_bar: int, price: float, role: str = "") -> SwingPoint:
    return SwingPoint(
        valid=True,
        identity=identity,
        scope=SCOPE_EXTERNAL,
        side=side,
        state=SWING_CONFIRMED,
        source_bar=source_bar,
        confirm_bar=source_bar + 2,
        price=price,
        atr_at_source=1.0,
        prominence_atr=1.0,
        distance_atr=1.0,
        quality=60.0,
        finalized=True,
        structural_role=role,
    )


def _candidate(event: str, direction: int, broken: SwingPoint, origin: SwingPoint, bar: int = 20) -> BreakCandidate:
    return BreakCandidate(
        valid=True,
        identity=1,
        scope=SCOPE_EXTERNAL,
        side=broken.side,
        intended_event_type=event,
        direction=direction,
        status="BREAK_CANDIDATE",
        candidate_bar=bar,
        expiry_bar=bar + 3,
        level=broken.price,
        buffer=0.10,
        candidate_atr=1.0,
        candidate_close=(broken.price + 0.25 if direction == 1 else broken.price - 0.25),
        first_close_distance_atr=0.25,
        initial_quality=70.0,
        broken_swing_identity=broken.identity,
        broken_source_bar=broken.source_bar,
        broken_quality=broken.quality or 50.0,
        broken_side=broken.side,
        origin_swing_identity=origin.identity,
        origin_source_bar=origin.source_bar,
        origin_quality=origin.quality or 50.0,
        origin_side=origin.side,
    )


def test_choch_enters_transition_not_full_reversal() -> None:
    protected_high = _s(1, SIDE_HIGH, 5, 110, ROLE_PROTECTED_HIGH)
    origin = _s(2, SIDE_LOW, 12, 100)
    swings = [protected_high, origin]
    ctx = StructureContext(direction=-1, state=STATE_BEARISH, protected_high_identity=1)

    out, event = finalize_confirmed_break(
        swings,
        ctx,
        _candidate(EVENT_CHOCH, 1, protected_high, origin),
        event_identity=7,
        event_bar=20,
        acceptance=80,
        follow_through=60,
    )

    assert out.state == STATE_TRANSITION_UP
    assert out.direction == 0
    assert out.protected_low_identity == 2
    assert event.event_type == EVENT_CHOCH


def test_bos_confirms_transition_and_promotes_previous_protected_to_strong() -> None:
    old_low = _s(1, SIDE_LOW, 7, 98, ROLE_PROTECTED_LOW)
    target = _s(2, SIDE_HIGH, 12, 110, ROLE_WEAK_HIGH)
    origin = _s(3, SIDE_LOW, 16, 102)
    swings = [old_low, target, origin]
    ctx = StructureContext(direction=0, state=STATE_TRANSITION_UP, protected_low_identity=1, weak_high_identity=2)

    out, _ = finalize_confirmed_break(
        swings,
        ctx,
        _candidate(EVENT_BOS, 1, target, origin),
        event_identity=8,
        event_bar=20,
        acceptance=75,
        follow_through=80,
    )

    assert out.state == STATE_BULLISH
    assert out.direction == 1
    assert out.protected_low_identity == 3
    assert out.strong_low_identity == 1
    assert out.evidence_text == "TRANSITION_CONFIRMED_BY_BOS"


def test_transition_fail_restores_old_main_direction() -> None:
    protected = _s(1, SIDE_LOW, 6, 95, ROLE_PROTECTED_LOW)
    origin = _s(2, SIDE_HIGH, 12, 105)
    swings = [protected, origin]
    ctx = StructureContext(direction=0, state=STATE_TRANSITION_UP, protected_low_identity=1)

    out, _ = finalize_confirmed_break(
        swings,
        ctx,
        _candidate(EVENT_TRANSITION_FAIL, -1, protected, origin),
        event_identity=9,
        event_bar=20,
        acceptance=70,
        follow_through=50,
    )

    assert out.state == STATE_BEARISH
    assert out.direction == -1
    assert out.protected_high_identity == 2


def test_role_normalization_chooses_latest_weak_after_protected_anchor() -> None:
    anchor = _s(1, SIDE_LOW, 5, 95, ROLE_PROTECTED_LOW)
    high_1 = _s(2, SIDE_HIGH, 8, 105)
    high_2 = _s(3, SIDE_HIGH, 12, 108)
    swings = [anchor, high_1, high_2]
    ctx = StructureContext(direction=1, state=STATE_BULLISH, protected_low_identity=1)

    out = normalize_directional_roles(swings, ctx)

    assert out.weak_high_identity == 3
    assert swings[2].structural_role == ROLE_WEAK_HIGH


def test_break_quality_immediate_vs_followthrough_formula() -> None:
    broken = _s(1, SIDE_HIGH, 5, 100)
    origin = _s(2, SIDE_LOW, 10, 95)
    candidate = _candidate(EVENT_BOS, 1, broken, origin)

    assert break_final_quality(candidate, 80, None) == 66
    assert break_final_quality(candidate, 80, 60) == 68


def test_candidate_confirmation_and_false_break_lifecycle() -> None:
    broken = _s(1, SIDE_HIGH, 5, 100)
    origin = _s(2, SIDE_LOW, 10, 95)
    candidate = _candidate(EVENT_BOS, 1, broken, origin, 20)
    config = BreakConfig(min_tick=0.01)

    confirmed = evaluate_break_candidate(candidate, config, bar_index=20, open_=100.05, high=100.8, low=100.0, close=100.7)
    assert confirmed.confirmed
    assert confirmed.final_quality is not None

    failed = evaluate_break_candidate(candidate, config, bar_index=21, open_=100.1, high=100.2, low=99.5, close=99.7)
    assert failed.false_break
    assert failed.failed


def test_sweep_and_export_codes() -> None:
    assert is_sweep(SIDE_HIGH, 100, 0.1, high=100.3, low=99.5, close=99.9)
    assert is_sweep(SIDE_LOW, 100, 0.1, high=100.5, low=99.7, close=100.1)
    assert export_state_code(STATE_BULLISH) == 2.0
    assert export_state_code(STATE_TRANSITION_UP) == 1.0
    assert export_state_code(STATE_TRANSITION_DOWN) == -1.0
