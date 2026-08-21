from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

import pandas as pd

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
from .models import (
    EvidenceContext,
    EvidenceDirection,
    EvidenceFamily,
    EvidenceRole,
    NormalizedLevel,
    ProvenanceType,
    TechnicalEvidenceItem,
    TechnicalEvidencePacket,
    direction_from_value,
    make_evidence_id,
    make_level_id,
    normalize_data_quality,
)


def _replace_items(packet: TechnicalEvidencePacket, mapper) -> TechnicalEvidencePacket:
    return replace(packet, evidence=tuple(mapper(item) for item in packet.evidence))


def _timestamps_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        left_ts = pd.Timestamp(left)
        right_ts = pd.Timestamp(right)
    except (TypeError, ValueError):
        return str(left) == str(right)
    if pd.isna(left_ts) or pd.isna(right_ts):
        return False
    left_aware = left_ts.tzinfo is not None
    right_aware = right_ts.tzinfo is not None
    if left_aware != right_aware:
        return False
    if left_aware:
        left_ts = left_ts.tz_convert("UTC")
        right_ts = right_ts.tz_convert("UTC")
    return bool(left_ts == right_ts)


def _require_context_timestamp(source_name: str, source_timestamp: Any, context: EvidenceContext) -> None:
    if not _timestamps_equal(source_timestamp, context.timestamp):
        raise ValueError(f"{source_name} timestamp must match EvidenceContext timestamp")


def _rewrite_level_sources(
    packet: TechnicalEvidencePacket,
    rewrites: dict[tuple[str, str], Callable[[NormalizedLevel], dict[str, Any]]],
) -> TechnicalEvidencePacket:
    """Clear false source-bar anchors while preserving the source-known metadata.

    Some upstream exports expose a confirmation/known index but not the original
    level-formation bar. TEL must not relabel that known index as source_bar because
    Tur-2 freshness would then invent a false event age.
    """

    id_map: dict[str, str] = {}
    levels: list[NormalizedLevel] = []
    for level in packet.levels:
        rewrite = rewrites.get((level.source_engine, level.level_type))
        if rewrite is None:
            levels.append(level)
            continue
        new_id = make_level_id(
            source_engine=level.source_engine,
            level_type=level.level_type,
            timeframe=level.timeframe,
            source_bar=None,
            known_bar=level.known_bar,
            price=level.price,
            lower=level.lower,
            upper=level.upper,
        )
        id_map[level.id] = new_id
        metadata = dict(level.raw_metadata)
        metadata.update(rewrite(level))
        levels.append(replace(level, id=new_id, source_bar=None, raw_metadata=metadata))

    if not id_map:
        return packet
    evidence = tuple(
        replace(item, level_refs=tuple(id_map.get(ref, ref) for ref in item.level_refs))
        for item in packet.evidence
    )
    return replace(packet, evidence=evidence, levels=tuple(levels))


def _anchor_liquidity_event(packet: TechnicalEvidencePacket, context: EvidenceContext) -> TechnicalEvidencePacket:
    """Anchor a sweep/reclaim export to the current confirmed bar.

    LiquidityEngine only populates latest_event_* from directional events observed
    on the current closed bar, so this is a causal event anchor rather than an
    inferred formation time.
    """

    if context.known_bar is None or not any(item.evidence_type == "LIQUIDITY_EVENT" for item in packet.evidence):
        return packet

    id_map: dict[str, str] = {}
    levels: list[NormalizedLevel] = []
    for level in packet.levels:
        if level.source_engine != "liquidity" or level.level_type != "LATEST_EVENT_LEVEL":
            levels.append(level)
            continue
        new_id = make_level_id(
            source_engine=level.source_engine,
            level_type=level.level_type,
            timeframe=level.timeframe,
            source_bar=context.known_bar,
            known_bar=level.known_bar,
            price=level.price,
            lower=level.lower,
            upper=level.upper,
        )
        id_map[level.id] = new_id
        metadata = dict(level.raw_metadata)
        metadata["event_bar"] = context.known_bar
        levels.append(
            replace(
                level,
                id=new_id,
                source_bar=context.known_bar,
                raw_metadata=metadata,
            )
        )

    evidence: list[TechnicalEvidenceItem] = []
    for item in packet.evidence:
        refs = tuple(id_map.get(ref, ref) for ref in item.level_refs)
        if item.evidence_type != "LIQUIDITY_EVENT":
            evidence.append(replace(item, level_refs=refs))
            continue
        evidence.append(
            replace(
                item,
                id=make_evidence_id(
                    source_engine=item.source_engine,
                    evidence_type=item.evidence_type,
                    timeframe=item.timeframe,
                    source_bar=context.known_bar,
                    known_bar=item.known_bar,
                    timestamp=item.timestamp,
                ),
                source_bar=context.known_bar,
                level_refs=refs,
            )
        )
    return replace(packet, evidence=tuple(evidence), levels=tuple(levels))


def _anchor_current_fvg_events(packet: TechnicalEvidencePacket, context: EvidenceContext) -> TechnicalEvidencePacket:
    """Anchor FVG/Engulfing EVENT ports to the current confirmed bar.

    The source facade emits an event only when its stored event index equals the
    current export index. Active zone formation time remains unknown because the
    permanent downstream contract does not export it.
    """

    if context.known_bar is None:
        return packet

    def anchor(item: TechnicalEvidenceItem) -> TechnicalEvidenceItem:
        if not item.evidence_type.endswith("_EVENT"):
            return item
        return replace(
            item,
            id=make_evidence_id(
                source_engine=item.source_engine,
                evidence_type=item.evidence_type,
                timeframe=item.timeframe,
                source_bar=context.known_bar,
                known_bar=item.known_bar,
                timestamp=item.timestamp,
            ),
            source_bar=context.known_bar,
        )

    return _replace_items(packet, anchor)


def adapt_market_structure(
    export: MarketStructureExport,
    context: EvidenceContext,
    *,
    result: EngineResult | None = None,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    if result is not None:
        _require_context_timestamp("Market Structure result", result.timestamp, context)
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
    _require_context_timestamp("MTF Story result", result.timestamp, context)
    packet = _raw.adapt_mtf_story(result, context, source_quality=source_quality)
    # MTF Story exports quality and a heuristic confidence, but no independent
    # strength port. Do not duplicate quality into strength.
    return _replace_items(packet, lambda item: replace(item, strength=None))


def adapt_liquidity(
    export: LiquidityExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_liquidity(export, context, source_quality=source_quality)
    packet = _replace_items(packet, lambda item: replace(item, strength=None))
    packet = _anchor_liquidity_event(packet, context)

    if export.latest_consume_side is None and export.latest_consume_level is None and export.latest_consume_identity is None:
        return packet

    canonical_quality, original_quality = normalize_data_quality(
        context.source_data_quality if source_quality is None else source_quality
    )
    source_bar = context.known_bar
    consume_level: NormalizedLevel | None = None
    level_refs: tuple[str, ...] = ()
    if export.latest_consume_level is not None:
        level_id = make_level_id(
            source_engine="liquidity",
            level_type="LATEST_CONSUMED_LEVEL",
            timeframe=context.timeframe,
            source_bar=source_bar,
            known_bar=context.known_bar,
            price=float(export.latest_consume_level),
            lower=None,
            upper=None,
        )
        consume_level = NormalizedLevel(
            id=level_id,
            source_engine="liquidity",
            level_type="LATEST_CONSUMED_LEVEL",
            timeframe=context.timeframe,
            price=float(export.latest_consume_level),
            polarity=direction_from_value(export.latest_consume_direction),
            source_bar=source_bar,
            known_bar=context.known_bar,
            timestamp=context.timestamp,
            state="CONSUME",
            raw_metadata={
                "side": export.latest_consume_side,
                "identity": export.latest_consume_identity,
                "event_bar": context.known_bar,
            },
        )
        level_refs = (level_id,)

    consume_item = TechnicalEvidenceItem(
        id=make_evidence_id(
            source_engine="liquidity",
            evidence_type="LIQUIDITY_CONSUME",
            timeframe=context.timeframe,
            source_bar=source_bar,
            known_bar=context.known_bar,
            timestamp=context.timestamp,
        ),
        source_engine="liquidity",
        evidence_type="LIQUIDITY_CONSUME",
        timeframe=context.timeframe,
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.LIQUIDITY,
        direction=direction_from_value(export.latest_consume_direction),
        strength=None,
        quality=None,
        data_quality=canonical_quality,
        source_data_quality=original_quality,
        source_bar=source_bar,
        known_bar=context.known_bar,
        timestamp=context.timestamp,
        level_refs=level_refs,
        provenance_type=ProvenanceType.ROOT,
        source_state="CONSUME",
        raw_export=dict(packet.evidence[0].raw_export) if packet.evidence else {},
    )
    return replace(
        packet,
        evidence=packet.evidence + (consume_item,),
        levels=packet.levels + (() if consume_level is None else (consume_level,)),
    )


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
    packet = _replace_items(packet, lambda item: replace(item, strength=None))
    return _rewrite_level_sources(
        packet,
        {
            ("support_resistance", "BREAK_BOUNDARY"): lambda _level: {
                "break_candidate_index": export.break_candidate_index,
                "break_confirmed_index": export.break_confirmed_index,
                "source_bar_status": "UNKNOWN_ORIGIN",
            }
        },
    )


def adapt_volume_participation(
    export: UnifiedParticipationExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_volume_participation(export, context, source_quality=source_quality)
    packet = _replace_items(packet, lambda item: replace(item, provenance_type=ProvenanceType.AGGREGATED))
    return _rewrite_level_sources(
        packet,
        {
            ("volume_participation", "LAST_PIVOT_HIGH"): lambda _level: {
                "known_index": export.last_pivot_high_known_index,
                "source_bar_status": "UNKNOWN_ORIGIN",
            },
            ("volume_participation", "LAST_PIVOT_LOW"): lambda _level: {
                "known_index": export.last_pivot_low_known_index,
                "source_bar_status": "UNKNOWN_ORIGIN",
            },
        },
    )


def adapt_stabil(
    export: StabilTrendExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_stabil(export, context, source_quality=source_quality)
    # Permanent Stabil downstream contract is STATE/HEALTH/RISK only. The final
    # facade carries internal direction for its own EngineResult, but TEL must not
    # leak that non-contract field into normalized downstream evidence.
    return _replace_items(
        packet,
        lambda item: replace(
            item,
            direction=EvidenceDirection.NEUTRAL,
            quality=None,
            provenance_type=ProvenanceType.AGGREGATED,
        ),
    )


def adapt_volatility(
    export: VolatilityBandsFibFinalExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_volatility(export, context, source_quality=source_quality)

    def normalize(item: TechnicalEvidenceItem) -> TechnicalEvidenceItem:
        direction = item.direction if item.evidence_type == "VOLATILITY_DIRECTION" else EvidenceDirection.NEUTRAL
        return replace(item, direction=direction, strength=None, provenance_type=ProvenanceType.AGGREGATED)

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
    packet = _replace_items(packet, lambda item: replace(item, strength=None))
    return _anchor_current_fvg_events(packet, context)


def adapt_ham(
    export: HamDashboardExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    packet = _raw.adapt_ham(export, context, source_quality=source_quality)
    return _replace_items(packet, lambda item: replace(item, provenance_type=ProvenanceType.AGGREGATED))


def merge_packets(context: EvidenceContext, packets) -> TechnicalEvidencePacket:
    packet_tuple = tuple(packets)
    for packet in packet_tuple:
        if not _timestamps_equal(packet.timestamp, context.timestamp):
            raise ValueError("all packets must share EvidenceContext timestamp")
    return _raw.merge_packets(context, packet_tuple)
