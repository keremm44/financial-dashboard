from financial_dashboard.engines.pattern_compression_engine import PatternExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceRole,
    adapt_pattern,
    independence_group_for,
)


def _ctx():
    return EvidenceContext(
        timeframe="2h",
        known_bar=120,
        timestamp="2026-08-20T18:00:00+03:00",
        source_data_quality="OK",
    )


def test_classic_pattern_bias_without_break_is_not_trigger_evidence():
    export = PatternExport(
        state=5,
        pattern_type=6,
        quality=72.0,
        classic_direction=1,
        break_state=0,
        break_level=105.0,
        break_strength=None,
        retest_state=0,
        retest_tolerance=0.4,
        identity=42.0,
    )
    packet = adapt_pattern(export, _ctx())

    assert len(packet.evidence) == 1
    geometry = packet.evidence[0]
    assert geometry.evidence_type == "PATTERN_GEOMETRY"
    assert geometry.role is EvidenceRole.STRUCTURE
    assert geometry.direction is EvidenceDirection.BULL
    assert geometry.strength is None
    assert all(item.role is not EvidenceRole.TRIGGER for item in packet.evidence)
    assert packet.levels[0].polarity is EvidenceDirection.NEUTRAL


def test_actual_break_and_retest_create_trigger_lifecycle_items():
    export = PatternExport(
        state=12,
        pattern_type=6,
        quality=82.0,
        classic_direction=1,
        break_state=-3,
        break_level=101.0,
        break_strength=76.0,
        retest_state=3,
        retest_tolerance=0.3,
        identity=43.0,
    )
    packet = adapt_pattern(export, _ctx())

    assert [item.evidence_type for item in packet.evidence] == [
        "PATTERN_BREAK",
        "PATTERN_RETEST",
        "PATTERN_GEOMETRY",
    ]
    break_item, retest_item, geometry = packet.evidence
    assert break_item.role is EvidenceRole.TRIGGER
    assert break_item.direction is EvidenceDirection.BEAR
    assert break_item.strength == 76.0
    assert retest_item.role is EvidenceRole.TRIGGER
    assert retest_item.direction is EvidenceDirection.BEAR
    assert retest_item.strength is None
    assert geometry.role is EvidenceRole.STRUCTURE
    assert geometry.direction is EvidenceDirection.BULL
    assert packet.levels[0].polarity is EvidenceDirection.BEAR
    assert len({independence_group_for(item) for item in packet.evidence}) == 1
