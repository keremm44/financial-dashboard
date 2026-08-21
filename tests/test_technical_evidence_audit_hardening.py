from types import SimpleNamespace

import pytest

from financial_dashboard.engines.liquidity_engine import LiquidityExport
from financial_dashboard.engines.market_structure_evidence import MarketStructureExport
from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.mtf_story_models import ContextState, MTFStoryResult, MTFStoryState, TriggerState
from financial_dashboard.engines.raw_indicator_dashboard_decision import HamDashboardExport
from financial_dashboard.engines.support_resistance_engine import SupportResistanceExport
from financial_dashboard.engines.volume_participation_final import UnifiedParticipationExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceGraphError,
    EvidenceRole,
    NormalizedLevel,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    adapt_ham,
    adapt_liquidity,
    adapt_market_structure,
    adapt_mtf_story,
    adapt_stabil,
    adapt_support_resistance,
    adapt_volume_participation,
    build_technical_evidence_bundle,
    merge_packets,
)


def _ctx(*, timeframe="2h", known_bar=120, timestamp="2026-08-20T18:00:00+03:00"):
    return EvidenceContext(
        timeframe=timeframe,
        known_bar=known_bar,
        timestamp=timestamp,
        source_data_quality="OK",
        is_closed=True,
        is_complete=True,
    )


def _item(id_: str, *, source_engine="market_structure", source_bar=None, known_bar=120, timestamp=None, level_refs=()):
    return TechnicalEvidenceItem(
        id=id_,
        source_engine=source_engine,
        evidence_type="TEST",
        timeframe="2h",
        role=EvidenceRole.STRUCTURE,
        family=EvidenceFamily.MARKET_STRUCTURE,
        direction=EvidenceDirection.BULL,
        source_bar=source_bar,
        known_bar=known_bar,
        timestamp=timestamp or _ctx().timestamp,
        level_refs=level_refs,
    )


def test_stabil_does_not_leak_private_direction_beyond_three_port_contract():
    export = SimpleNamespace(
        state_code=2,
        health=83.0,
        risk=24.0,
        direction=Direction.UP,
    )
    packet = adapt_stabil(export, _ctx())

    assert {item.direction for item in packet.evidence} == {EvidenceDirection.NEUTRAL}
    assert all(set(item.raw_export) == {"state", "health", "risk"} for item in packet.evidence)
    assert all(item.quality is None for item in packet.evidence)


def test_mtf_story_quality_is_not_duplicated_into_strength():
    result = MTFStoryResult(
        state=MTFStoryState.TREND_CONTINUATION,
        timestamp=_ctx().timestamp,
        dominant_direction=Direction.UP,
        macro_direction=Direction.UP,
        context_state=ContextState.BULLISH_CONTEXT,
        trigger_state=TriggerState.BULLISH_TRIGGER,
        quality=84.0,
        confidence=0.78,
    )
    item = adapt_mtf_story(result, _ctx()).evidence[0]

    assert item.quality == 84.0
    assert item.strength is None
    assert item.raw_export["confidence"] == 0.78


def test_source_result_timestamp_must_match_evidence_context():
    export = MarketStructureExport(
        external_state=1.0,
        internal_state=1.0,
        evidence_score=70.0,
        external_protected_low=None,
        external_protected_high=None,
        external_weak_low=None,
        external_weak_high=None,
        internal_protected_low=None,
        internal_protected_high=None,
        internal_weak_low=None,
        internal_weak_high=None,
    )
    result = EngineResult(
        engine="market_structure",
        state="BULLISH",
        timestamp="2026-08-20T16:00:00+03:00",
        direction=Direction.UP,
    )
    with pytest.raises(ValueError, match="timestamp"):
        adapt_market_structure(export, _ctx(), result=result)


def test_mtf_story_timestamp_must_match_evidence_context():
    result = MTFStoryResult(
        state=MTFStoryState.RANGE_MIXED,
        timestamp="2026-08-20T16:00:00+03:00",
        dominant_direction=Direction.NEUTRAL,
        macro_direction=Direction.NEUTRAL,
        context_state=ContextState.MIXED_CONTEXT,
        trigger_state=TriggerState.NO_TRIGGER,
        quality=50.0,
        confidence=0.5,
    )
    with pytest.raises(ValueError, match="timestamp"):
        adapt_mtf_story(result, _ctx())


def test_liquidity_consume_becomes_separate_structured_trigger_evidence():
    export = LiquidityExport(
        nearest_bsl=108.0,
        nearest_ssl=99.0,
        latest_consume_side="BSL",
        latest_consume_level=105.0,
        latest_consume_identity="BSL:42",
        latest_consume_direction=1,
        quality=65.0,
    )
    packet = adapt_liquidity(export, _ctx())
    consume = next(item for item in packet.evidence if item.evidence_type == "LIQUIDITY_CONSUME")

    assert consume.role is EvidenceRole.TRIGGER
    assert consume.direction is EvidenceDirection.BULL
    assert consume.strength is None
    assert consume.quality is None
    assert consume.source_bar == _ctx().known_bar
    level = packet.level_by_id(consume.level_refs[0])
    assert level is not None
    assert level.level_type == "LATEST_CONSUMED_LEVEL"
    assert level.raw_metadata["identity"] == "BSL:42"


def test_volume_pivot_known_index_is_not_relabelled_as_source_bar():
    export = UnifiedParticipationExport(
        state="PARTICIPATION_NEUTRAL",
        last_pivot_high=110.0,
        last_pivot_high_known_index=95,
        last_pivot_low=90.0,
        last_pivot_low_known_index=92,
    )
    packet = adapt_volume_participation(export, _ctx())
    high = next(level for level in packet.levels if level.level_type == "LAST_PIVOT_HIGH")
    low = next(level for level in packet.levels if level.level_type == "LAST_PIVOT_LOW")

    assert high.source_bar is None and low.source_bar is None
    assert high.raw_metadata["known_index"] == 95
    assert low.raw_metadata["known_index"] == 92
    assert high.id in packet.evidence[0].level_refs
    assert low.id in packet.evidence[0].level_refs


def test_support_resistance_break_indices_are_metadata_not_false_source_bar():
    export = SupportResistanceExport(
        state="RANGE_BREAK_CONFIRMED",
        break_direction=1,
        break_candidate_index=100,
        break_confirmed_index=101,
        break_boundary=105.0,
    )
    packet = adapt_support_resistance(export, _ctx())
    boundary = next(level for level in packet.levels if level.level_type == "BREAK_BOUNDARY")

    assert boundary.source_bar is None
    assert boundary.raw_metadata["break_candidate_index"] == 100
    assert boundary.raw_metadata["break_confirmed_index"] == 101
    assert boundary.id in packet.evidence[0].level_refs


def test_merge_packets_rejects_same_bar_with_different_timestamp():
    first_ctx = _ctx(timestamp="2026-08-20T18:00:00+03:00")
    second_ctx = _ctx(timestamp="2026-08-20T19:00:00+03:00")
    first = adapt_ham(HamDashboardExport(1, 40.0, 1, 20.0), first_ctx)
    second = adapt_ham(HamDashboardExport(1, 42.0, 1, 22.0), second_ctx)

    with pytest.raises(ValueError, match="timestamp"):
        merge_packets(first_ctx, (first, second))


def test_bundle_coalesces_same_timeframe_engine_packets_for_same_snapshot():
    first = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp=_ctx().timestamp,
        evidence=(_item("ms"),),
    )
    second_item = TechnicalEvidenceItem(
        id="liq",
        source_engine="liquidity",
        evidence_type="LIQUIDITY_EVENT",
        timeframe="2h",
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.LIQUIDITY,
        direction=EvidenceDirection.BEAR,
        known_bar=120,
        timestamp=_ctx().timestamp,
    )
    second = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp=_ctx().timestamp,
        evidence=(second_item,),
    )

    bundle = build_technical_evidence_bundle((first, second))
    assert len(bundle.packets) == 1
    assert {item.id for item in bundle.evidence} == {"ms", "liq"}


def test_bundle_rejects_two_different_snapshots_for_same_timeframe():
    old = TechnicalEvidencePacket(timeframe="2h", known_bar=119, timestamp="2026-08-20T16:00:00+03:00")
    current = TechnicalEvidencePacket(timeframe="2h", known_bar=120, timestamp=_ctx().timestamp)

    with pytest.raises(EvidenceGraphError, match="multiple snapshots"):
        build_technical_evidence_bundle((old, current))


def test_cross_engine_reference_cannot_invent_unanchored_level_freshness():
    level = NormalizedLevel(
        id="support",
        source_engine="support_resistance",
        level_type="SUPPORT",
        timeframe="2h",
        price=100.0,
        known_bar=120,
        timestamp=_ctx().timestamp,
    )
    evidence = _item("structure", source_engine="market_structure", source_bar=110, level_refs=(level.id,))
    packet = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=120,
        timestamp=_ctx().timestamp,
        evidence=(evidence,),
        levels=(level,),
    )

    bundle = build_technical_evidence_bundle((packet,))
    enriched = bundle.level_by_id(level.id)
    record = next(record for record in bundle.freshness if record.target_id == level.id)
    assert enriched is not None and enriched.freshness is None
    assert record.anchor == "UNKNOWN"


def test_packet_level_as_of_guard_catches_empty_future_snapshot():
    packet = TechnicalEvidencePacket(
        timeframe="2h",
        known_bar=121,
        timestamp="2026-08-20T20:00:00+03:00",
    )
    with pytest.raises(EvidenceGraphError, match="packet beyond as-of bar"):
        build_technical_evidence_bundle((packet,), as_of_known_bars={"2H": 120})
    with pytest.raises(EvidenceGraphError, match="packet beyond as-of timestamp"):
        build_technical_evidence_bundle((packet,), as_of_timestamp=_ctx().timestamp)
