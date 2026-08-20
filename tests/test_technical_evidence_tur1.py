from __future__ import annotations

from types import SimpleNamespace

import pytest

from financial_dashboard.engines.auction_engine import AuctionExport, AuctionNode
from financial_dashboard.engines.fvg_engulfing_final import EngulfingSideExport, FvgEngulfingExport, FvgSideExport
from financial_dashboard.engines.liquidity_engine import LiquidityExport
from financial_dashboard.engines.market_structure_evidence import MarketStructureExport
from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.mtf_story_models import ContextState, MTFStoryResult, MTFStoryState, TriggerState
from financial_dashboard.engines.order_block import OrderBlockExport, OrderBlockSideExport
from financial_dashboard.engines.pattern_compression_engine import PatternExport
from financial_dashboard.engines.raw_indicator_dashboard_decision import HamDashboardExport
from financial_dashboard.engines.support_resistance_engine import SupportResistanceExport
from financial_dashboard.engines.volatility_bands_fib_final import VolatilityBandsFibFinalExport
from financial_dashboard.engines.volume_participation_final import UnifiedParticipationExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDataQuality,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceRole,
    ProvenanceType,
    TechnicalEvidenceBuilder,
    adapt_auction,
    adapt_fvg_engulfing,
    adapt_ham,
    adapt_liquidity,
    adapt_market_structure,
    adapt_mtf_story,
    adapt_order_block,
    adapt_pattern,
    adapt_stabil,
    adapt_support_resistance,
    adapt_volatility,
    adapt_volume_participation,
    merge_packets,
    normalize_data_quality,
)


def _ctx(**changes):
    values = {
        "timeframe": "2h",
        "known_bar": 120,
        "timestamp": "2026-08-20T18:00:00+03:00",
        "source_data_quality": "OK",
        "is_closed": True,
        "is_complete": True,
    }
    values.update(changes)
    return EvidenceContext(**values)


def test_market_structure_adapter_preserves_export_and_levels():
    export = MarketStructureExport(
        external_state=1.0,
        internal_state=1.0,
        evidence_score=82.0,
        external_protected_low=100.0,
        external_protected_high=None,
        external_weak_low=None,
        external_weak_high=106.0,
        internal_protected_low=101.0,
        internal_protected_high=None,
        internal_weak_low=None,
        internal_weak_high=104.0,
    )
    result = EngineResult(
        engine="market_structure",
        state="BULLISH",
        timestamp=_ctx().timestamp,
        direction=Direction.UP,
        score=82.0,
        quality=77.0,
        is_confirmed=True,
    )
    packet = adapt_market_structure(export, _ctx(), result=result)

    assert len(packet.evidence) == 1
    item = packet.evidence[0]
    assert item.role is EvidenceRole.STRUCTURE
    assert item.family is EvidenceFamily.MARKET_STRUCTURE
    assert item.direction is EvidenceDirection.BULL
    assert item.strength == 82.0
    assert item.quality == 77.0
    assert item.raw_export["handshake"] == 314159.0
    assert {level.level_type for level in packet.levels} == {
        "EXTERNAL_PROTECTED_LOW",
        "EXTERNAL_WEAK_HIGH",
        "INTERNAL_PROTECTED_LOW",
        "INTERNAL_WEAK_HIGH",
    }
    assert set(item.level_refs) == {level.id for level in packet.levels}


def test_pattern_adapter_keeps_break_and_retest_geometry():
    export = PatternExport(
        state=4,
        pattern_type=7,
        quality=71.0,
        classic_direction=1,
        break_state=2,
        break_level=103.0,
        break_strength=78.0,
        retest_state=1,
        retest_tolerance=0.40,
        identity=55.0,
    )
    packet = adapt_pattern(export, _ctx(), break_direction=1)
    item = packet.evidence[0]
    level = packet.levels[0]

    assert item.role is EvidenceRole.TRIGGER
    assert item.direction is EvidenceDirection.BULL
    assert item.raw_export["retest_tolerance"] == 0.40
    assert level.price == 103.0
    assert level.lower == pytest.approx(102.60)
    assert level.upper == pytest.approx(103.40)


def test_mtf_story_is_derived_but_not_discarded():
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
    packet = adapt_mtf_story(result, _ctx())
    item = packet.evidence[0]

    assert item.provenance_type is ProvenanceType.DERIVED
    assert item.role is EvidenceRole.CONTEXT
    assert item.strength == 84.0
    assert item.raw_export["confidence"] == 0.78


def test_liquidity_adapter_keeps_levels_event_and_identity():
    export = LiquidityExport(
        nearest_bsl=108.0,
        nearest_ssl=99.0,
        active_bsl_count=2,
        active_ssl_count=3,
        latest_event_side="SSL",
        latest_event_state="RECLAIM",
        latest_event_level=100.0,
        latest_event_identity="SSL:42",
        latest_event_direction=1,
        quality=76.0,
    )
    packet = adapt_liquidity(export, _ctx())
    item = packet.evidence[0]

    assert item.role is EvidenceRole.TRIGGER
    assert item.direction is EvidenceDirection.BULL
    assert item.raw_export["active_ssl_count"] == 3
    assert {level.level_type for level in packet.levels} == {
        "NEAREST_BSL",
        "NEAREST_SSL",
        "LATEST_EVENT_LEVEL",
    }
    latest = next(level for level in packet.levels if level.level_type == "LATEST_EVENT_LEVEL")
    assert latest.raw_metadata["identity"] == "SSL:42"


def test_auction_adapter_preserves_profile_nodes_without_merging_close_levels():
    export = AuctionExport(
        poc=101.0,
        vah=104.0,
        val=98.0,
        reaction_state="ACCEPTED",
        migration_state="MIG_UP",
        balance_state="IMBALANCE_UP",
        balance_direction=1,
        quality=74.0,
        regime_strength=69.0,
        primary_zone_kind="HVN",
        primary_zone_low=100.90,
        primary_zone_high=101.10,
        primary_zone_score=80.0,
        hvn_nodes=(
            AuctionNode("HVN", 101.00, 100.95, 101.05, 82.0, 10, 9, 11),
            AuctionNode("HVN", 101.01, 100.96, 101.06, 79.0, 12, 12, 13),
        ),
    )
    packet = adapt_auction(export, _ctx())

    hvn_levels = [level for level in packet.levels if level.level_type.startswith("HVN_")]
    assert len(hvn_levels) == 2
    assert hvn_levels[0].id != hvn_levels[1].id
    assert packet.evidence[0].role is EvidenceRole.LOCATION
    assert packet.evidence[0].strength == 69.0


def test_support_resistance_adapter_keeps_zones_and_break_reference():
    export = SupportResistanceExport(
        state="RANGE_BREAK_CONFIRMED",
        upper_top=110.0,
        upper_bottom=109.0,
        lower_top=101.0,
        lower_bottom=100.0,
        mid_price=105.0,
        quality=81.0,
        break_direction=1,
        break_confirmed_index=118,
        break_boundary=110.0,
        nearest_support_low=108.5,
        nearest_support_high=109.0,
    )
    packet = adapt_support_resistance(export, _ctx())
    item = packet.evidence[0]

    assert item.direction is EvidenceDirection.BULL
    assert item.role is EvidenceRole.LOCATION
    break_level = next(level for level in packet.levels if level.level_type == "BREAK_BOUNDARY")
    assert break_level.source_bar == 118
    assert break_level.price == 110.0


def test_volume_participation_adapter_keeps_absorption_and_break_fields():
    export = UnifiedParticipationExport(
        state="PARTICIPATION_UP_CONFIRMED",
        support_direction=1,
        engine_direction=1,
        quality=79.0,
        magnitude_quality=72.0,
        break_direction=1,
        break_stage="SUPPORTED",
        break_level=105.0,
        break_reference_source="MS",
        absorption_side="LOWER",
        absorption_stage="CONFIRMED",
        absorption_reference_level=101.0,
        absorption_reference_source="PIVOT",
        last_pivot_high=106.0,
        last_pivot_high_known_index=115,
        last_pivot_low=100.0,
        last_pivot_low_known_index=110,
    )
    packet = adapt_volume_participation(export, _ctx())
    item = packet.evidence[0]

    assert item.role is EvidenceRole.CONFIRMATION
    assert item.family is EvidenceFamily.VOLUME
    assert item.direction is EvidenceDirection.BULL
    assert item.strength == 72.0
    assert item.raw_export["absorption_reference_source"] == "PIVOT"
    assert {level.level_type for level in packet.levels} >= {"BREAK_LEVEL", "ABSORPTION_REFERENCE"}


def test_stabil_adapter_exposes_only_official_three_port_payload():
    export = SimpleNamespace(
        state_code=2,
        health=83.0,
        risk=24.0,
        direction=Direction.UP,
    )
    packet = adapt_stabil(export, _ctx())

    assert len(packet.evidence) == 2
    assert {item.role for item in packet.evidence} == {EvidenceRole.CONTEXT, EvidenceRole.RISK}
    assert all(set(item.raw_export) == {"state", "health", "risk"} for item in packet.evidence)
    assert packet.evidence[0].raw_export == {"state": 2, "health": 83.0, "risk": 24.0}


def test_volatility_adapter_exposes_only_official_six_ports():
    export = VolatilityBandsFibFinalExport(
        regime=3,
        direction=1.0,
        quality=68.0,
        band_state=4,
        band_agreement=1,
        fib_state=5,
        data_quality="OK",
        structure_state=6,
        structure_quality=99.0,
        active_swing_start=90.0,
    )
    packet = adapt_volatility(export, _ctx())

    assert len(packet.evidence) == 5
    expected = {"regime", "direction", "quality", "band_state", "band_agreement", "fib_state"}
    assert all(set(item.raw_export) == expected for item in packet.evidence)
    assert all("structure_state" not in item.raw_export for item in packet.evidence)
    assert all(item.data_quality is EvidenceDataQuality.OK for item in packet.evidence)


def test_order_block_adapter_keeps_bull_and_bear_remaining_zones():
    export = OrderBlockExport(
        bull=OrderBlockSideExport(state=1.0, top=103.0, bottom=101.0, fill=0.35, source_bar=90.0),
        bear=OrderBlockSideExport(state=-1.0, top=110.0, bottom=108.0, fill=0.20, source_bar=95.0),
    )
    packet = adapt_order_block(export, _ctx())

    assert len(packet.evidence) == 2
    assert {item.direction for item in packet.evidence} == {EvidenceDirection.BULL, EvidenceDirection.BEAR}
    assert {level.source_bar for level in packet.levels} == {90, 95}
    assert all("fill" in level.raw_metadata for level in packet.levels)


def test_fvg_engulfing_adapter_preserves_location_and_terminal_event_separately():
    export = FvgEngulfingExport(
        bull_fvg=FvgSideExport(state=3, top=104.0, bottom=103.0, quality=77.0, fill=0.30, event=4),
        bear_engulf=EngulfingSideExport(state=-2, top=109.0, bottom=108.0, quality=64.0, retrace=0.25, event=None),
    )
    packet = adapt_fvg_engulfing(export, _ctx())

    assert {item.evidence_type for item in packet.evidence} == {
        "BULL_FVG",
        "BULL_FVG_EVENT",
        "BEAR_ENGULF",
    }
    trigger = next(item for item in packet.evidence if item.evidence_type == "BULL_FVG_EVENT")
    assert trigger.role is EvidenceRole.TRIGGER
    assert trigger.level_refs == next(item for item in packet.evidence if item.evidence_type == "BULL_FVG").level_refs


def test_ham_adapter_has_exact_four_port_raw_contract_and_two_semantic_items():
    export = HamDashboardExport(
        momentum_state=2,
        momentum_score=68.0,
        timing_state=-1,
        timing_score=-31.0,
    )
    packet = adapt_ham(export, _ctx())

    assert len(packet.evidence) == 2
    momentum = next(item for item in packet.evidence if item.family is EvidenceFamily.MOMENTUM)
    timing = next(item for item in packet.evidence if item.family is EvidenceFamily.TIMING)
    assert momentum.direction is EvidenceDirection.BULL
    assert momentum.strength == 68.0
    assert timing.direction is EvidenceDirection.BEAR
    assert timing.strength == 31.0
    assert set(momentum.raw_export) == {"momentum_state", "momentum_score", "timing_state", "timing_score"}
    assert "price_family" not in momentum.raw_export
    assert "flow_family" not in timing.raw_export


def test_data_quality_preserves_canonical_and_source_specific_value():
    assert normalize_data_quality("DATA_OK") == (EvidenceDataQuality.OK, "DATA_OK")
    assert normalize_data_quality("DATA_LIMITED") == (EvidenceDataQuality.DATA_LIMITED, "DATA_LIMITED")
    assert normalize_data_quality("SOURCE_GAP") == (EvidenceDataQuality.SOURCE_GAP, "SOURCE_GAP")
    assert normalize_data_quality("UNSUPPORTED_TIMEFRAME") == (
        EvidenceDataQuality.UNSUPPORTED_TIMEFRAME,
        "UNSUPPORTED_TIMEFRAME",
    )
    assert normalize_data_quality("ENGINE_PRIVATE_STATUS") == (
        EvidenceDataQuality.UNKNOWN,
        "ENGINE_PRIVATE_STATUS",
    )


def test_merge_packet_is_lossless_and_ids_are_deterministic():
    ham = HamDashboardExport(2, 68.0, -1, -31.0)
    liquidity = LiquidityExport(nearest_bsl=108.0, nearest_ssl=99.0, quality=70.0)

    first = merge_packets(_ctx(), [adapt_ham(ham, _ctx()), adapt_liquidity(liquidity, _ctx())])
    second = merge_packets(_ctx(), [adapt_ham(ham, _ctx()), adapt_liquidity(liquidity, _ctx())])

    assert first == second
    assert len(first.evidence) == 3
    assert len(first.levels) == 2
    assert [item.id for item in first.evidence] == [item.id for item in second.evidence]
    assert [level.id for level in first.levels] == [level.id for level in second.levels]


def test_builder_freezes_on_open_or_incomplete_bar():
    packet = adapt_ham(HamDashboardExport(2, 68.0, 1, 20.0), _ctx())
    builder = TechnicalEvidenceBuilder()
    confirmed = builder.update(_ctx(), packet)
    assert confirmed == packet

    open_ctx = _ctx(known_bar=121, timestamp="2026-08-20T20:00:00+03:00", is_closed=False)
    open_packet = adapt_ham(HamDashboardExport(-2, -90.0, -2, -80.0), open_ctx)
    assert builder.update(open_ctx, open_packet) == confirmed
    assert builder.snapshot() == confirmed

    gap_ctx = _ctx(known_bar=122, timestamp="2026-08-20T22:00:00+03:00", is_complete=False)
    gap_packet = adapt_ham(HamDashboardExport(-2, -95.0, -2, -90.0), gap_ctx)
    assert builder.update(gap_ctx, gap_packet) == confirmed


def test_packet_rejects_dangling_level_reference():
    packet = adapt_liquidity(LiquidityExport(nearest_bsl=108.0), _ctx())
    broken_item = packet.evidence[0].__class__(
        **{
            name: getattr(packet.evidence[0], name)
            for name in packet.evidence[0].__dataclass_fields__
            if name != "level_refs"
        },
        level_refs=("missing-level",),
    )
    with pytest.raises(ValueError, match="dangling normalized level"):
        packet.__class__(
            timeframe=packet.timeframe,
            known_bar=packet.known_bar,
            timestamp=packet.timestamp,
            evidence=(broken_item,),
            levels=packet.levels,
        )
