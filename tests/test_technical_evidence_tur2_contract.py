from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.engines.fvg_engulfing_final import FvgEngulfingExport, FvgSideExport
from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.order_block import OrderBlockExport, OrderBlockSideExport
from financial_dashboard.engines.raw_indicator_dashboard_decision import HamDashboardExport
from financial_dashboard.engines.volatility_bands_fib_final import VolatilityBandsFibFinalExport
from financial_dashboard.engines.volume_participation_final import UnifiedParticipationExport
from financial_dashboard.engines.market_structure_evidence import MarketStructureExport
from financial_dashboard.technical_evidence import (
    EvidenceContext,
    EvidenceDirection,
    ProvenanceType,
    adapt_fvg_engulfing,
    adapt_ham,
    adapt_market_structure,
    adapt_order_block,
    adapt_stabil,
    adapt_volatility,
    adapt_volume_participation,
    build_technical_evidence_bundle,
    independence_group_for,
)


def _ctx(**changes) -> EvidenceContext:
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


def test_ham_official_ports_are_aggregated_but_remain_separate_independence_groups():
    packet = adapt_ham(HamDashboardExport(2, 68.0, -1, -31.0), _ctx())
    bundle = build_technical_evidence_bundle((packet,))

    assert len(bundle.evidence) == 2
    assert all(item.provenance_type is ProvenanceType.AGGREGATED for item in bundle.evidence)
    groups = {independence_group_for(item) for item in bundle.evidence}
    assert groups == {"HAM_MOMENTUM", "HAM_TIMING"}
    assert bundle.audit.aggregated_count == 2
    assert bundle.audit.independence_group_count == 2


def test_stabil_three_port_contract_is_aggregated_context_risk_not_new_root_votes():
    export = SimpleNamespace(
        state_code=2,
        health=83.0,
        risk=24.0,
        direction=Direction.UP,
    )
    packet = adapt_stabil(export, _ctx())
    bundle = build_technical_evidence_bundle((packet,))

    assert len(bundle.evidence) == 2
    assert all(item.provenance_type is ProvenanceType.AGGREGATED for item in bundle.evidence)
    assert bundle.audit.aggregated_count == 2
    assert bundle.audit.root_count == 0
    assert bundle.audit.multi_item_independence_groups[0][0] == "STABIL_TREND_CORE"


def test_volume_participation_final_export_is_marked_aggregated_without_losing_payload():
    export = UnifiedParticipationExport(
        state="PARTICIPATION_UP_CONFIRMED",
        support_direction=1,
        engine_direction=1,
        quality=75.0,
        magnitude_quality=72.0,
        break_direction=1,
        break_stage="SUPPORTED",
        break_level=103.5,
    )
    packet = adapt_volume_participation(export, _ctx())
    item = packet.evidence[0]

    assert item.provenance_type is ProvenanceType.AGGREGATED
    assert item.raw_export["break_level"] == 103.5
    assert item.direction is EvidenceDirection.BULL


def test_volatility_ports_share_one_independence_group_not_five_flat_votes():
    export = VolatilityBandsFibFinalExport(
        regime=3,
        direction=1.0,
        quality=68.0,
        band_state=4,
        band_agreement=1,
        fib_state=5,
        data_quality="OK",
    )
    packet = adapt_volatility(export, _ctx())
    bundle = build_technical_evidence_bundle((packet,))

    assert len(bundle.evidence) == 5
    assert {independence_group_for(item) for item in bundle.evidence} == {"VOLATILITY_BANDS_FIB_CORE"}
    group, ids = bundle.audit.multi_item_independence_groups[0]
    assert group == "VOLATILITY_BANDS_FIB_CORE"
    assert len(ids) == 5
    directional = [item for item in bundle.evidence if item.direction is not EvidenceDirection.NEUTRAL]
    assert [item.evidence_type for item in directional] == ["VOLATILITY_DIRECTION"]


def test_order_block_source_bar_produces_causal_freshness_but_fvg_does_not_invent_age():
    ob_packet = adapt_order_block(
        OrderBlockExport(
            bull=OrderBlockSideExport(state=1.0, top=103.0, bottom=101.0, fill=0.35, source_bar=110.0)
        ),
        _ctx(),
    )
    fvg_packet = adapt_fvg_engulfing(
        FvgEngulfingExport(
            bull_fvg=FvgSideExport(state=3, top=104.0, bottom=103.0, quality=77.0, fill=0.30, event=4)
        ),
        _ctx(),
    )
    bundle = build_technical_evidence_bundle((ob_packet, fvg_packet))

    ob = next(item for item in bundle.evidence if item.source_engine == "order_block")
    fvg = next(item for item in bundle.evidence if item.evidence_type == "BULL_FVG")
    assert ob.source_bar == 110
    assert ob.freshness is not None
    assert fvg.source_bar is None
    assert fvg.freshness is None


def test_market_structure_remains_root_and_raw_handshake_survives_tur2():
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
    bundle = build_technical_evidence_bundle((packet,))
    item = bundle.evidence[0]

    assert item.provenance_type is ProvenanceType.ROOT
    assert item.raw_export["handshake"] == 314159.0
    assert bundle.lineage_by_id(item.id).root_ids == (item.id,)
