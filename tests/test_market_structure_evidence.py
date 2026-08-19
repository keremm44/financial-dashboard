from financial_dashboard.engines.market_structure import SCOPE_EXTERNAL, SIDE_HIGH, SIDE_LOW, SWING_CONFIRMED, SwingPoint
from financial_dashboard.engines.market_structure_evidence import HANDSHAKE, export_snapshot, structure_score
from financial_dashboard.engines.market_structure_state import (
    EVENT_FALSE_BREAK,
    ROLE_PROTECTED_LOW,
    ROLE_WEAK_HIGH,
    STATE_BULLISH,
    STATE_NEUTRAL,
    STATE_TRANSITION_UP,
    StructureContext,
    StructureEvent,
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


def test_structure_score_alignment_is_high_and_transition_is_capped() -> None:
    external = StructureContext(
        direction=1,
        state=STATE_BULLISH,
        last_confirmed_high_identity=1,
        last_confirmed_low_identity=2,
        protected_low_identity=2,
        weak_high_identity=1,
        quality=80,
    )
    internal = StructureContext(direction=1, state=STATE_BULLISH)

    assert structure_score(external, internal, None, None, bar_index=100) >= 70

    transition = StructureContext(
        direction=0,
        state=STATE_TRANSITION_UP,
        last_confirmed_high_identity=1,
        last_confirmed_low_identity=2,
        protected_low_identity=2,
        weak_high_identity=1,
        quality=90,
    )
    assert structure_score(transition, internal, None, None, bar_index=100) <= 79


def test_false_break_penalizes_latest_evidence() -> None:
    external = StructureContext(
        direction=1,
        state=STATE_BULLISH,
        last_confirmed_high_identity=1,
        last_confirmed_low_identity=2,
        protected_low_identity=2,
        weak_high_identity=1,
        quality=80,
    )
    internal = StructureContext(direction=1, state=STATE_BULLISH)
    base = structure_score(external, internal, None, None, bar_index=100)
    event = StructureEvent(valid=True, identity=5, event_type=EVENT_FALSE_BREAK, direction=-1, event_bar=100)

    assert structure_score(external, internal, event, None, bar_index=100) < base


def test_export_contract_hides_conflicted_scope_and_uses_handshake() -> None:
    swings = [
        _s(1, SIDE_HIGH, 5, 110, ROLE_WEAK_HIGH),
        _s(2, SIDE_LOW, 8, 100, ROLE_PROTECTED_LOW),
    ]
    external = StructureContext(direction=1, state=STATE_BULLISH, protected_low_identity=2, weak_high_identity=1)
    internal = StructureContext(direction=0, state=STATE_NEUTRAL)

    out = export_snapshot(swings, [], external, internal, evidence_score=77)
    assert out.external_state == 2.0
    assert out.external_protected_low == 100
    assert out.external_weak_high == 110
    assert out.evidence_score == 77
    assert out.handshake == HANDSHAKE

    external.conflict_text = "PROTECTED_LOW_MISSING"
    hidden = export_snapshot(swings, [], external, internal, evidence_score=77)
    assert hidden.external_state is None
    assert hidden.evidence_score is None
