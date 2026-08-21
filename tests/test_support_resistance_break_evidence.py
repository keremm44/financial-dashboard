from __future__ import annotations

import pytest

from financial_dashboard.engines.support_resistance_engine import RangeState, SupportResistanceExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceRole,
    adapt_support_resistance,
    independence_group_for,
)


TS = "2026-08-20T18:00:00+03:00"


def _ctx(*, known_bar: int = 120) -> EvidenceContext:
    return EvidenceContext(
        timeframe="2h",
        known_bar=known_bar,
        timestamp=TS,
        source_data_quality="OK",
        is_closed=True,
        is_complete=True,
    )


def test_confirmed_range_break_is_separate_trigger_from_location_geometry():
    export = SupportResistanceExport(
        state=RangeState.BREAK_CONFIRMED.value,
        range_identity=4,
        upper_bottom=103.5,
        upper_top=104.5,
        lower_bottom=98.0,
        lower_top=99.0,
        quality=73.0,
        break_direction=1,
        break_candidate_index=108,
        break_confirmed_index=110,
        break_boundary=104.5,
    )
    packet = adapt_support_resistance(export, _ctx())

    location = next(item for item in packet.evidence if item.evidence_type == "SUPPORT_RESISTANCE_RANGE")
    trigger = next(item for item in packet.evidence if item.evidence_type == "SUPPORT_RESISTANCE_BREAK")

    assert location.role is EvidenceRole.LOCATION
    assert trigger.role is EvidenceRole.TRIGGER
    assert trigger.direction is EvidenceDirection.BULL
    assert trigger.source_bar == 110
    assert trigger.source_state == RangeState.BREAK_CONFIRMED.value
    assert trigger.strength is None
    assert trigger.quality is None
    assert independence_group_for(location) == independence_group_for(trigger) == "SUPPORT_RESISTANCE_CORE"

    boundary = packet.level_by_id(trigger.level_refs[0])
    assert boundary is not None
    assert boundary.level_type == "BREAK_BOUNDARY"
    # Boundary formation/origin is not exported; do not misuse the break event bar.
    assert boundary.source_bar is None
    assert boundary.raw_metadata["break_confirmed_index"] == 110


def test_unconfirmed_range_state_does_not_emit_break_trigger():
    export = SupportResistanceExport(
        state=RangeState.BREAK_CANDIDATE.value,
        break_direction=1,
        break_candidate_index=118,
        break_boundary=104.5,
    )
    packet = adapt_support_resistance(export, _ctx())

    assert all(item.evidence_type != "SUPPORT_RESISTANCE_BREAK" for item in packet.evidence)


def test_future_confirmed_break_index_is_rejected():
    export = SupportResistanceExport(
        state=RangeState.BREAK_CONFIRMED.value,
        break_direction=-1,
        break_confirmed_index=121,
        break_boundary=98.0,
    )

    with pytest.raises(ValueError, match="source_bar"):
        adapt_support_resistance(export, _ctx(known_bar=120))
