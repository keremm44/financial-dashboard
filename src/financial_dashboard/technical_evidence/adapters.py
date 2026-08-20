from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

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

from .models import (
    EvidenceContext,
    EvidenceDataQuality,
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
    plain_payload,
    score_0_100,
    signed_strength,
)


@dataclass(slots=True)
class _PacketParts:
    evidence: list[TechnicalEvidenceItem]
    levels: list[NormalizedLevel]


def _quality(context: EvidenceContext, override: Any = None) -> tuple[EvidenceDataQuality, str | None]:
    return normalize_data_quality(context.source_data_quality if override is None else override)


def _payload(value: Any, *, include: Iterable[str] | None = None) -> dict[str, Any]:
    raw = plain_payload(value)
    if not isinstance(raw, dict):
        return {"value": raw}
    if include is None:
        return raw
    return {name: raw.get(name) for name in include}


def _level(
    parts: _PacketParts,
    context: EvidenceContext,
    *,
    source_engine: str,
    level_type: str,
    price: float | None = None,
    lower: float | None = None,
    upper: float | None = None,
    polarity: EvidenceDirection = EvidenceDirection.NEUTRAL,
    quality: float | None = None,
    source_bar: int | None = None,
    state: str | int | None = None,
    raw_metadata: dict[str, Any] | None = None,
) -> str | None:
    if price is None and lower is None and upper is None:
        return None
    level_id = make_level_id(
        source_engine=source_engine,
        level_type=level_type,
        timeframe=context.timeframe,
        source_bar=source_bar,
        known_bar=context.known_bar,
        price=price,
        lower=lower,
        upper=upper,
    )
    parts.levels.append(
        NormalizedLevel(
            id=level_id,
            source_engine=source_engine,
            level_type=level_type,
            timeframe=context.timeframe,
            price=price,
            lower=lower,
            upper=upper,
            polarity=polarity,
            quality=score_0_100(quality),
            source_bar=source_bar,
            known_bar=context.known_bar,
            timestamp=context.timestamp,
            state=state,
            raw_metadata=raw_metadata or {},
        )
    )
    return level_id


def _item(
    parts: _PacketParts,
    context: EvidenceContext,
    *,
    source_engine: str,
    evidence_type: str,
    role: EvidenceRole,
    family: EvidenceFamily,
    direction: EvidenceDirection = EvidenceDirection.NEUTRAL,
    strength: float | None = None,
    quality: float | None = None,
    data_quality: Any = None,
    level_refs: Iterable[str | None] = (),
    provenance_type: ProvenanceType = ProvenanceType.ROOT,
    depends_on: Iterable[str] = (),
    source_state: str | int | None = None,
    source_bar: int | None = None,
    raw_export: dict[str, Any] | None = None,
) -> TechnicalEvidenceItem:
    canonical_quality, source_quality = _quality(context, data_quality)
    evidence_id = make_evidence_id(
        source_engine=source_engine,
        evidence_type=evidence_type,
        timeframe=context.timeframe,
        source_bar=source_bar,
        known_bar=context.known_bar,
        timestamp=context.timestamp,
    )
    item = TechnicalEvidenceItem(
        id=evidence_id,
        source_engine=source_engine,
        evidence_type=evidence_type,
        timeframe=context.timeframe,
        role=role,
        family=family,
        direction=direction,
        strength=score_0_100(strength),
        quality=score_0_100(quality),
        freshness=None,
        data_quality=canonical_quality,
        source_data_quality=source_quality,
        source_bar=source_bar,
        known_bar=context.known_bar,
        timestamp=context.timestamp,
        level_refs=tuple(ref for ref in level_refs if ref is not None),
        provenance_type=provenance_type,
        depends_on=tuple(depends_on),
        source_state=source_state,
        raw_export=raw_export or {},
    )
    parts.evidence.append(item)
    return item


def adapt_market_structure(
    export: MarketStructureExport,
    context: EvidenceContext,
    *,
    result: EngineResult | None = None,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    refs: list[str | None] = []
    for field_name, polarity in (
        ("external_protected_low", EvidenceDirection.BULL),
        ("external_protected_high", EvidenceDirection.BEAR),
        ("external_weak_low", EvidenceDirection.BEAR),
        ("external_weak_high", EvidenceDirection.BULL),
        ("internal_protected_low", EvidenceDirection.BULL),
        ("internal_protected_high", EvidenceDirection.BEAR),
        ("internal_weak_low", EvidenceDirection.BEAR),
        ("internal_weak_high", EvidenceDirection.BULL),
    ):
        refs.append(
            _level(
                parts,
                context,
                source_engine="market_structure",
                level_type=field_name.upper(),
                price=getattr(export, field_name),
                polarity=polarity,
                quality=result.quality if result is not None else None,
            )
        )
    direction = direction_from_value(result.direction if result is not None else 0)
    _item(
        parts,
        context,
        source_engine="market_structure",
        evidence_type="MARKET_STRUCTURE",
        role=EvidenceRole.STRUCTURE,
        family=EvidenceFamily.MARKET_STRUCTURE,
        direction=direction,
        strength=export.evidence_score,
        quality=result.quality if result is not None else None,
        data_quality=source_quality,
        level_refs=refs,
        source_state=result.state if result is not None else export.external_state,
        raw_export=_payload(export),
    )
    return _packet(context, parts)


def adapt_pattern(
    export: PatternExport,
    context: EvidenceContext,
    *,
    break_direction: int = 0,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    break_ref = _level(
        parts,
        context,
        source_engine="pattern_compression",
        level_type="BREAK_LEVEL",
        price=export.break_level,
        lower=(export.break_level - export.retest_tolerance) if export.break_level is not None and export.retest_tolerance is not None else None,
        upper=(export.break_level + export.retest_tolerance) if export.break_level is not None and export.retest_tolerance is not None else None,
        polarity=direction_from_value(break_direction or export.classic_direction),
        quality=export.quality,
        state=export.break_state,
        raw_metadata={"retest_tolerance": export.retest_tolerance},
    )
    direction = direction_from_value(break_direction or export.classic_direction)
    _item(
        parts,
        context,
        source_engine="pattern_compression",
        evidence_type="PATTERN_COMPRESSION",
        role=EvidenceRole.TRIGGER,
        family=EvidenceFamily.PATTERN,
        direction=direction,
        strength=export.break_strength if export.break_strength is not None else export.quality,
        quality=export.quality,
        data_quality=source_quality,
        level_refs=(break_ref,),
        source_state=export.state,
        raw_export=_payload(export),
    )
    return _packet(context, parts)


def adapt_mtf_story(
    result: MTFStoryResult,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    _item(
        parts,
        context,
        source_engine="mtf_story",
        evidence_type="MTF_STORY",
        role=EvidenceRole.CONTEXT,
        family=EvidenceFamily.MTF_STORY,
        direction=direction_from_value(result.dominant_direction),
        strength=result.quality,
        quality=result.quality,
        data_quality=source_quality,
        provenance_type=ProvenanceType.DERIVED,
        source_state=result.state.value,
        raw_export=_payload(result),
    )
    return _packet(context, parts)


def adapt_liquidity(
    export: LiquidityExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    bsl = _level(
        parts,
        context,
        source_engine="liquidity",
        level_type="NEAREST_BSL",
        price=export.nearest_bsl,
        polarity=EvidenceDirection.BEAR,
        quality=export.quality,
    )
    ssl = _level(
        parts,
        context,
        source_engine="liquidity",
        level_type="NEAREST_SSL",
        price=export.nearest_ssl,
        polarity=EvidenceDirection.BULL,
        quality=export.quality,
    )
    latest = _level(
        parts,
        context,
        source_engine="liquidity",
        level_type="LATEST_EVENT_LEVEL",
        price=export.latest_event_level,
        polarity=direction_from_value(export.latest_event_direction),
        quality=export.quality,
        state=export.latest_event_state,
        raw_metadata={"identity": export.latest_event_identity, "side": export.latest_event_side},
    )
    role = EvidenceRole.TRIGGER if export.latest_event_state is not None else EvidenceRole.LOCATION
    _item(
        parts,
        context,
        source_engine="liquidity",
        evidence_type="LIQUIDITY_EVENT" if export.latest_event_state is not None else "LIQUIDITY_LEVELS",
        role=role,
        family=EvidenceFamily.LIQUIDITY,
        direction=direction_from_value(export.latest_event_direction),
        strength=export.quality,
        quality=export.quality,
        data_quality=source_quality,
        level_refs=(bsl, ssl, latest),
        source_state=export.latest_event_state,
        raw_export=_payload(export),
    )
    return _packet(context, parts)


def adapt_auction(
    export: AuctionExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    refs: list[str | None] = [
        _level(parts, context, source_engine="auction", level_type="POC", price=export.poc, quality=export.quality),
        _level(parts, context, source_engine="auction", level_type="VAH", price=export.vah, polarity=EvidenceDirection.BEAR, quality=export.quality),
        _level(parts, context, source_engine="auction", level_type="VAL", price=export.val, polarity=EvidenceDirection.BULL, quality=export.quality),
        _level(
            parts,
            context,
            source_engine="auction",
            level_type=f"PRIMARY_{export.primary_zone_kind or 'ZONE'}",
            lower=export.primary_zone_low,
            upper=export.primary_zone_high,
            quality=export.primary_zone_score,
            state=export.balance_state,
        ),
    ]
    for kind, nodes in (("HVN", export.hvn_nodes), ("LVN", export.lvn_nodes)):
        for index, node in enumerate(nodes):
            refs.append(
                _level(
                    parts,
                    context,
                    source_engine="auction",
                    level_type=f"{kind}_{index}",
                    price=node.center_price,
                    lower=node.low_price,
                    upper=node.high_price,
                    quality=node.score,
                    raw_metadata=_payload(node),
                )
            )
    _item(
        parts,
        context,
        source_engine="auction",
        evidence_type="AUCTION_PROFILE",
        role=EvidenceRole.LOCATION,
        family=EvidenceFamily.AUCTION,
        direction=direction_from_value(export.balance_direction),
        strength=export.regime_strength,
        quality=export.quality,
        data_quality=source_quality,
        level_refs=refs,
        source_state=export.balance_state or export.reaction_state or export.migration_state,
        raw_export=_payload(export),
    )
    return _packet(context, parts)


def adapt_support_resistance(
    export: SupportResistanceExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    refs: list[str | None] = []
    zone_specs = (
        ("RANGE_UPPER", export.upper_bottom, export.upper_top, EvidenceDirection.BEAR),
        ("RANGE_LOWER", export.lower_bottom, export.lower_top, EvidenceDirection.BULL),
        ("NEAREST_SUPPORT", export.nearest_support_low, export.nearest_support_high, EvidenceDirection.BULL),
        ("NEAREST_RESISTANCE", export.nearest_resistance_low, export.nearest_resistance_high, EvidenceDirection.BEAR),
        ("ROLE_REVERSAL_SUPPORT", export.role_reversal_support_low, export.role_reversal_support_high, EvidenceDirection.BULL),
        ("ROLE_REVERSAL_RESISTANCE", export.role_reversal_resistance_low, export.role_reversal_resistance_high, EvidenceDirection.BEAR),
    )
    for name, lower, upper, polarity in zone_specs:
        refs.append(
            _level(
                parts,
                context,
                source_engine="support_resistance",
                level_type=name,
                lower=lower,
                upper=upper,
                polarity=polarity,
                quality=export.quality,
                state=export.state,
            )
        )
    refs.extend(
        [
            _level(parts, context, source_engine="support_resistance", level_type="RANGE_MID", price=export.mid_price, quality=export.quality),
            _level(
                parts,
                context,
                source_engine="support_resistance",
                level_type="BREAK_BOUNDARY",
                price=export.break_boundary,
                polarity=direction_from_value(export.break_direction),
                quality=export.quality,
                source_bar=export.break_confirmed_index or export.break_candidate_index,
            ),
        ]
    )
    _item(
        parts,
        context,
        source_engine="support_resistance",
        evidence_type="SUPPORT_RESISTANCE_RANGE",
        role=EvidenceRole.LOCATION,
        family=EvidenceFamily.SUPPORT_RESISTANCE,
        direction=direction_from_value(export.break_direction),
        strength=export.quality,
        quality=export.quality,
        data_quality=source_quality,
        level_refs=refs,
        source_state=export.state,
        raw_export=_payload(export),
    )
    return _packet(context, parts)


def adapt_volume_participation(
    export: UnifiedParticipationExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    refs = [
        _level(
            parts,
            context,
            source_engine="volume_participation",
            level_type="BREAK_LEVEL",
            price=export.break_level,
            polarity=direction_from_value(export.break_direction),
            quality=export.quality,
            state=export.break_stage,
            raw_metadata={"reference_source": export.break_reference_source},
        ),
        _level(
            parts,
            context,
            source_engine="volume_participation",
            level_type="ABSORPTION_REFERENCE",
            price=export.absorption_reference_level,
            polarity=EvidenceDirection.BEAR if str(export.absorption_side).upper() == "UPPER" else EvidenceDirection.BULL if str(export.absorption_side).upper() == "LOWER" else EvidenceDirection.NEUTRAL,
            quality=export.quality,
            state=export.absorption_stage,
            raw_metadata={"reference_source": export.absorption_reference_source},
        ),
        _level(parts, context, source_engine="volume_participation", level_type="LAST_PIVOT_HIGH", price=export.last_pivot_high, polarity=EvidenceDirection.BEAR, source_bar=export.last_pivot_high_known_index),
        _level(parts, context, source_engine="volume_participation", level_type="LAST_PIVOT_LOW", price=export.last_pivot_low, polarity=EvidenceDirection.BULL, source_bar=export.last_pivot_low_known_index),
    ]
    direction = direction_from_value(export.engine_direction or export.support_direction or export.participation_direction)
    _item(
        parts,
        context,
        source_engine="volume_participation",
        evidence_type="VOLUME_PARTICIPATION",
        role=EvidenceRole.CONFIRMATION,
        family=EvidenceFamily.VOLUME,
        direction=direction,
        strength=export.magnitude_quality if export.magnitude_quality is not None else export.quality,
        quality=export.quality,
        data_quality=source_quality,
        level_refs=refs,
        source_state=export.state,
        raw_export=_payload(export),
    )
    return _packet(context, parts)


def adapt_stabil(
    export: StabilTrendExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    official = {
        "state": export.state_code,
        "health": export.health,
        "risk": export.risk,
    }
    _item(
        parts,
        context,
        source_engine="stabil_trend",
        evidence_type="STABIL_CONTEXT",
        role=EvidenceRole.CONTEXT,
        family=EvidenceFamily.STABIL,
        direction=direction_from_value(export.direction),
        strength=export.health,
        quality=export.health,
        data_quality=source_quality,
        source_state=export.state_code,
        raw_export=official,
    )
    _item(
        parts,
        context,
        source_engine="stabil_trend",
        evidence_type="STABIL_RISK",
        role=EvidenceRole.RISK,
        family=EvidenceFamily.STABIL,
        direction=EvidenceDirection.NEUTRAL,
        strength=export.risk,
        quality=export.health,
        data_quality=source_quality,
        source_state=export.state_code,
        raw_export=official,
    )
    return _packet(context, parts)


def adapt_volatility(
    export: VolatilityBandsFibFinalExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    official_fields = ("regime", "direction", "quality", "band_state", "band_agreement", "fib_state")
    official = _payload(export, include=official_fields)
    quality_source = export.data_quality if source_quality is None else source_quality
    normalized_direction = direction_from_value(export.direction)
    specs = (
        ("VOLATILITY_REGIME", EvidenceRole.RISK, normalized_direction, export.quality, export.regime),
        ("VOLATILITY_DIRECTION", EvidenceRole.CONFIRMATION, normalized_direction, export.quality, export.direction),
        ("BAND_STATE", EvidenceRole.LOCATION, normalized_direction, export.quality, export.band_state),
        ("BAND_AGREEMENT", EvidenceRole.CONFIRMATION, normalized_direction, export.quality, export.band_agreement),
        ("FIB_STATE", EvidenceRole.LOCATION, normalized_direction, export.quality, export.fib_state),
    )
    for evidence_type, role, direction, strength, state in specs:
        _item(
            parts,
            context,
            source_engine="volatility_bands_fib",
            evidence_type=evidence_type,
            role=role,
            family=EvidenceFamily.VOLATILITY,
            direction=direction,
            strength=strength,
            quality=export.quality,
            data_quality=quality_source,
            source_state=state,
            raw_export=official,
        )
    return _packet(context, parts)


def adapt_order_block(
    export: OrderBlockExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    raw = _payload(export)
    for name, side, polarity in (
        ("BULL_ORDER_BLOCK", export.bull, EvidenceDirection.BULL),
        ("BEAR_ORDER_BLOCK", export.bear, EvidenceDirection.BEAR),
    ):
        if side.state is None and side.top is None and side.bottom is None:
            continue
        source_bar = int(side.source_bar) if side.source_bar is not None else None
        ref = _level(
            parts,
            context,
            source_engine="order_block",
            level_type=name,
            lower=side.bottom,
            upper=side.top,
            polarity=polarity,
            source_bar=source_bar,
            state=side.state,
            raw_metadata={"fill": side.fill},
        )
        _item(
            parts,
            context,
            source_engine="order_block",
            evidence_type=name,
            role=EvidenceRole.LOCATION,
            family=EvidenceFamily.ORDER_BLOCK,
            direction=polarity,
            strength=None,
            quality=None,
            data_quality=source_quality,
            level_refs=(ref,),
            source_state=side.state,
            source_bar=source_bar,
            raw_export=raw,
        )
    return _packet(context, parts)


def adapt_fvg_engulfing(
    export: FvgEngulfingExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    raw = _payload(export)
    for name, side, polarity, fill_field in (
        ("BULL_FVG", export.bull_fvg, EvidenceDirection.BULL, "fill"),
        ("BEAR_FVG", export.bear_fvg, EvidenceDirection.BEAR, "fill"),
        ("BULL_ENGULF", export.bull_engulf, EvidenceDirection.BULL, "retrace"),
        ("BEAR_ENGULF", export.bear_engulf, EvidenceDirection.BEAR, "retrace"),
    ):
        if side.state is None and side.top is None and side.bottom is None and side.event is None:
            continue
        fill_value = getattr(side, fill_field)
        ref = _level(
            parts,
            context,
            source_engine="fvg_engulfing",
            level_type=name,
            lower=side.bottom,
            upper=side.top,
            polarity=polarity,
            quality=side.quality,
            state=side.state,
            raw_metadata={fill_field: fill_value, "event": side.event},
        )
        _item(
            parts,
            context,
            source_engine="fvg_engulfing",
            evidence_type=name,
            role=EvidenceRole.LOCATION,
            family=EvidenceFamily.FVG,
            direction=polarity,
            strength=side.quality,
            quality=side.quality,
            data_quality=source_quality,
            level_refs=(ref,),
            source_state=side.state,
            raw_export=raw,
        )
        if side.event is not None:
            _item(
                parts,
                context,
                source_engine="fvg_engulfing",
                evidence_type=f"{name}_EVENT",
                role=EvidenceRole.TRIGGER,
                family=EvidenceFamily.FVG,
                direction=polarity,
                strength=side.quality,
                quality=side.quality,
                data_quality=source_quality,
                level_refs=(ref,),
                source_state=side.event,
                raw_export=raw,
            )
    return _packet(context, parts)


def adapt_ham(
    export: HamDashboardExport,
    context: EvidenceContext,
    *,
    source_quality: Any = None,
) -> TechnicalEvidencePacket:
    parts = _PacketParts([], [])
    official = _payload(export)
    momentum_direction, momentum_strength = signed_strength(export.momentum_score)
    if export.momentum_score is None:
        momentum_direction = direction_from_value(export.momentum_state)
    timing_direction, timing_strength = signed_strength(export.timing_score)
    if export.timing_score is None:
        timing_direction = direction_from_value(export.timing_state)
    _item(
        parts,
        context,
        source_engine="ham_dashboard",
        evidence_type="HAM_MOMENTUM",
        role=EvidenceRole.CONFIRMATION,
        family=EvidenceFamily.MOMENTUM,
        direction=momentum_direction,
        strength=momentum_strength,
        quality=None,
        data_quality=source_quality,
        source_state=export.momentum_state,
        raw_export=official,
    )
    _item(
        parts,
        context,
        source_engine="ham_dashboard",
        evidence_type="HAM_TIMING",
        role=EvidenceRole.TIMING,
        family=EvidenceFamily.TIMING,
        direction=timing_direction,
        strength=timing_strength,
        quality=None,
        data_quality=source_quality,
        source_state=export.timing_state,
        raw_export=official,
    )
    return _packet(context, parts)


def merge_packets(context: EvidenceContext, packets: Iterable[TechnicalEvidencePacket]) -> TechnicalEvidencePacket:
    evidence: list[TechnicalEvidenceItem] = []
    levels: list[NormalizedLevel] = []
    for packet in packets:
        if packet.timeframe != context.timeframe or packet.known_bar != context.known_bar:
            raise ValueError("all packets must share timeframe and known_bar")
        evidence.extend(packet.evidence)
        levels.extend(packet.levels)
    return TechnicalEvidencePacket(
        timeframe=context.timeframe,
        known_bar=context.known_bar,
        timestamp=context.timestamp,
        evidence=tuple(evidence),
        levels=tuple(levels),
    )


def _packet(context: EvidenceContext, parts: _PacketParts) -> TechnicalEvidencePacket:
    return TechnicalEvidencePacket(
        timeframe=context.timeframe,
        known_bar=context.known_bar,
        timestamp=context.timestamp,
        evidence=tuple(parts.evidence),
        levels=tuple(parts.levels),
    )
