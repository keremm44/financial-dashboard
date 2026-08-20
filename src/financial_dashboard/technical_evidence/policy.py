from __future__ import annotations

from dataclasses import replace
from typing import Any

from financial_dashboard.engines.auction_engine import AuctionExport
from financial_dashboard.engines.fvg_engulfing_final import FvgEngulfingExport
from financial_dashboard.engines.liquidity_engine import LiquidityExport
from financial_dashboard.engines.market_structure_evidence import MarketStructureExport
from financial_dashboard.engines.models import EngineResult
from financial_dashboard.engines.mtf_story_models import MTFStoryResult
from financial_dashboard.engines.order_block import OrderBlockExport
from financial_dashboard.engines.pattern_compression_engine import PatternExport
from financial_dashboard.engines.raw_indicator_dashboard_decision import HamDashboardExport
from financial_dashboard.engines.stabil_trend_final import StabilTrendExport
from financial_dashboard.engines.support_resistance_engine import SupportResistanceExport
from financial_dashboard.engines.volatility_bands_fib_final import VolatilityBandsFibFinalExport
from financial_dashboard.engines.volume_participation_final import UnifiedParticipationExport

from . import adapters as _raw
from .models import EvidenceContext, EvidenceDirection, TechnicalEvidencePacket


def _replace_items(packet: TechnicalEvidencePacket, mapper) -> TechnicalEvidencePacket:
    return replace(packet, evidence=tuple(mapper(item) for item in packet.evidence))


def adapt_market_structure(
    export: MarketStructureExport,
    context: EvidenceContext,
    *,
    result: EngineResult | None = None,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    return _raw.adapt_market_structure(export, context, result=result, source_quality=source_quality)


def adapt_pattern(
    export: PatternExport,
    context: EvidenceContext,
    *,
    break_direction: int = 0,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_pattern(export, context, break_direction=break_direction, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, strength=export.break_strength))


def adapt_mtf_story(
    result: MTFStoryResult,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_mtf_story(result, context, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, strength=None))


def adapt_liquidity(
    export: LiquidityExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_liquidity(export, context, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, strength=None))


def adapt_auction(
    export: AuctionExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    return _raw.adapt_auction(export, context, source_quality=source_quality)


def adapt_support_resistance(
    export: SupportResistanceExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_support_resistance(export, context, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, strength=None))


def adapt_volume_participation(
    export: UnifiedParticipationExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    return _raw.adapt_volume_participation(export, context, source_quality=source_quality)


def adapt_stabil(
    export: StabilTrendExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_stabil(export, context, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, quality=None))


def adapt_volatility(
    export: VolatilityBandsFibFinalExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_volatility(export, context, source_quality=source_quality)

    def normalize(item):
        direction = item.direction if item.evidence_type == "VOLATILITY_DIRECTION" else EvidenceDirection.NEUTRAL
        return replace(item, direction=direction, strength=None)

    return _replace_items(packet, normalize)


def adapt_order_block(
    export: OrderBlockExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    return _raw.adapt_order_block(export, context, source_quality=source_quality)


def adapt_fvg_engulfing(
    export: FvgEngulfingExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_fvg_engulfing(export, context, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, strength=None))


def adapt_ham(
    export: HamDashboardExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    return _raw.adapt_ham(export, context, source_quality=source_quality)


def merge_packets(context: EvidenceContext, packets):
    return _raw.merge_packets(context, packets)
