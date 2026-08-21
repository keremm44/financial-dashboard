from __future__ import annotations

import pytest

from financial_dashboard.engines.market_structure_evidence import (
    MarketStructureEventExport,
    MarketStructureExport,
    export_structure_event,
)
from financial_dashboard.engines.market_structure_state import EVENT_CHOCH, EVENT_FALSE_BREAK, StructureEvent
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceRole,
    adapt_market_structure,
    independence_group_for,
)


TS = "2026-08-20T18:00:00+03:00"


def _ctx(*, known_bar: int = 50) -> EvidenceContext:
    return EvidenceContext(
        timeframe="2h",
        known_bar=known_bar,
        timestamp=TS,
        source_data_quality="OK",
        is_closed=True,
        is_complete=True,
    )


def _export(**changes) -> MarketStructureExport:
    values = dict(
        external_state=1.0,
        internal_state=1.0,
        evidence_score=75.0,
        external_protected_low=99.0,
        external_protected_high=None,
        external_weak_low=None,
        external_weak_high=105.0,
        internal_protected_low=100.0,
        internal_protected_high=None,
        internal_weak_low=None,
        internal_weak_high=103.0,
    )
    values.update(changes)
    return MarketStructureExport(**values)


def test_structure_event_export_preserves_confirmed_event_fields():
    source = StructureEvent(
        valid=True,
        identity=7,
        scope="EXTERNAL",
        event_type=EVENT_CHOCH,
        direction=1,
        event_bar=42,
        broken_swing_identity=11,
        broken_source_bar=30,
        origin_swing_identity=12,
        origin_source_bar=35,
        level=101.5,
        origin_price=98.0,
        quality=81.0,
        evidence_text="confirmed choch",
    )

    out = export_structure_event(source)

    assert out.scope == "EXTERNAL"
    assert out.event_type == EVENT_CHOCH
    assert out.direction == 1
    assert out.event_bar == 42
    assert out.identity == 7
    assert out.level == 101.5
    assert out.quality == 81.0
    assert out.broken_source_bar == 30
    assert out.origin_source_bar == 35
    assert out.evidence_text == "confirmed choch"


def test_invalid_or_missing_structure_event_exports_empty_contract():
    assert export_structure_event(None) == MarketStructureEventExport()
    assert export_structure_event(StructureEvent()) == MarketStructureEventExport()


def test_tel_emits_structured_market_structure_event_as_trigger_with_causal_level():
    event = MarketStructureEventExport(
        scope="EXTERNAL",
        event_type=EVENT_CHOCH,
        direction=1,
        event_bar=42,
        identity=7,
        level=101.5,
        quality=81.0,
        broken_swing_identity=11,
        broken_source_bar=30,
        origin_swing_identity=12,
        origin_source_bar=35,
        origin_price=98.0,
        evidence_text="confirmed choch",
    )
    packet = adapt_market_structure(_export(external_event=event), _ctx())

    structure = next(item for item in packet.evidence if item.evidence_type == "MARKET_STRUCTURE")
    trigger = next(item for item in packet.evidence if item.evidence_type == "MARKET_STRUCTURE_EXTERNAL_EVENT")

    assert structure.role is EvidenceRole.STRUCTURE
    assert trigger.role is EvidenceRole.TRIGGER
    assert trigger.direction is EvidenceDirection.BULL
    assert trigger.source_bar == 42
    assert trigger.source_state == EVENT_CHOCH
    assert trigger.quality == 81.0
    assert trigger.raw_export["identity"] == 7
    assert trigger.raw_export["broken_source_bar"] == 30
    assert trigger.raw_export["origin_source_bar"] == 35
    assert independence_group_for(structure) == independence_group_for(trigger) == "MARKET_STRUCTURE_CORE"

    level = packet.level_by_id(trigger.level_refs[0])
    assert level is not None
    assert level.level_type == "EXTERNAL_STRUCTURE_EVENT_LEVEL"
    assert level.price == 101.5
    assert level.source_bar == 42
    assert level.raw_metadata["identity"] == 7


def test_internal_and_external_events_remain_same_independence_group_not_flat_votes():
    external = MarketStructureEventExport(
        scope="EXTERNAL",
        event_type=EVENT_CHOCH,
        direction=1,
        event_bar=42,
        identity=7,
        level=101.5,
        quality=81.0,
    )
    internal = MarketStructureEventExport(
        scope="INTERNAL",
        event_type=EVENT_FALSE_BREAK,
        direction=-1,
        event_bar=44,
        identity=9,
        level=102.0,
        quality=60.0,
    )
    packet = adapt_market_structure(
        _export(external_event=external, internal_event=internal),
        _ctx(),
    )
    triggers = [item for item in packet.evidence if item.role is EvidenceRole.TRIGGER]

    assert len(triggers) == 2
    assert {item.direction for item in triggers} == {EvidenceDirection.BULL, EvidenceDirection.BEAR}
    assert {independence_group_for(item) for item in triggers} == {"MARKET_STRUCTURE_CORE"}


def test_future_structure_event_bar_is_rejected_by_tel_contract():
    event = MarketStructureEventExport(
        scope="EXTERNAL",
        event_type=EVENT_CHOCH,
        direction=1,
        event_bar=51,
        identity=7,
        level=101.5,
        quality=81.0,
    )

    with pytest.raises(ValueError, match="source_bar"):
        adapt_market_structure(_export(external_event=event), _ctx(known_bar=50))
