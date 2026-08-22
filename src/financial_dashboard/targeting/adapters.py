from __future__ import annotations

from hashlib import sha1
from typing import Any, Mapping

from financial_dashboard.engines.fvg_engulfing_models import EngulfingState, FvgState
from financial_dashboard.engines.liquidity_models import LiquidityPoolState, LiquiditySide

from .models import (
    LiquidityScope,
    TargetEvidence,
    TargetEvidenceFamily,
    TargetEvidenceType,
    TargetRole,
)


def _uid(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _metadata(
    confirmations: Mapping[str, tuple[Any, Any]],
    identity: str,
    *,
    fallback: Any,
) -> tuple[Any, Any]:
    return confirmations.get(identity, (fallback, fallback))


def liquidity_evidence(
    *,
    symbol: str,
    timeframe: str,
    engine,
    confirmations: Mapping[str, tuple[Any, Any]],
    scope_by_identity: Mapping[str, LiquidityScope] | None = None,
) -> tuple[TargetEvidence, ...]:
    out: list[TargetEvidence] = []
    scope_by_identity = scope_by_identity or {}
    eligible_states = {LiquidityPoolState.ACTIVE, LiquidityPoolState.TESTED}
    for pool in engine.pools:
        confirmed_at, available_at = _metadata(
            confirmations,
            pool.identity,
            fallback=pool.updated_at,
        )
        first_touch = pool.touches[0]
        role = TargetRole.MAGNET
        out.append(
            TargetEvidence(
                uid=f"TE-{_uid(symbol, timeframe, 'LIQ', pool.identity)}",
                symbol=symbol,
                timeframe=timeframe,
                evidence_type=TargetEvidenceType.LIQUIDITY,
                family=TargetEvidenceFamily.STRUCTURAL,
                roles=(role,),
                low=float(pool.level),
                high=float(pool.level),
                anchor_price=float(pool.level),
                origin_index=int(first_touch.bar_index),
                origin_time=first_touch.timestamp,
                confirmed_at=confirmed_at,
                available_at=available_at,
                source_state=pool.state.value,
                target_eligible=pool.state in eligible_states,
                native_origin_id=f"LIQ:{timeframe}:{pool.identity}",
                origin_event_id=f"LIQ:{timeframe}:{pool.identity}",
                source_identity=pool.identity,
                formation_atr=None,
                source_quality=None,
                liquidity_scope=scope_by_identity.get(pool.identity, LiquidityScope.UNCLASSIFIED),
            )
        )
    return tuple(out)


def order_block_evidence(
    *,
    symbol: str,
    timeframe: str,
    engine,
    confirmations: Mapping[str, tuple[Any, Any]],
) -> tuple[TargetEvidence, ...]:
    out: list[TargetEvidence] = []
    for record in engine.records:
        identity = f"OB:{timeframe}:{record.source_index}:{1 if record.bullish else -1}"
        confirmed_at, available_at = _metadata(
            confirmations,
            identity,
            fallback=record.source_time,
        )
        active_top, active_bottom = engine._active_remaining_zone(record)
        low, high = sorted((float(active_bottom), float(active_top)))
        roles = (
            (TargetRole.DEMAND, TargetRole.REACTION)
            if record.bullish
            else (TargetRole.SUPPLY, TargetRole.REACTION)
        )
        out.append(
            TargetEvidence(
                uid=f"TE-{_uid(symbol, identity)}",
                symbol=symbol,
                timeframe=timeframe,
                evidence_type=TargetEvidenceType.ORDER_BLOCK,
                family=TargetEvidenceFamily.SUPPLY_DEMAND,
                roles=roles,
                low=low,
                high=high,
                anchor_price=(low + high) / 2.0,
                origin_index=int(record.source_index),
                origin_time=record.source_time,
                confirmed_at=confirmed_at,
                available_at=available_at,
                source_state="ACTIVE" if record.active else "CANDIDATE",
                target_eligible=bool(record.active and record.fill_ratio < engine.config.fill_cancel_threshold),
                native_origin_id=identity,
                origin_event_id=identity,
                source_identity=identity,
                formation_atr=None,
                source_quality=float(record.score),
            )
        )
    return tuple(out)


def fvg_engulfing_evidence(
    *,
    symbol: str,
    timeframe: str,
    engine,
    confirmations: Mapping[str, tuple[Any, Any]],
) -> tuple[TargetEvidence, ...]:
    out: list[TargetEvidence] = []
    fvg_eligible = {
        FvgState.ACTIVE,
        FvgState.FIRST_TEST,
        FvgState.PARTIAL_FILL,
        FvgState.DEEP_TEST,
        FvgState.REACTION,
    }
    engulf_eligible = {
        EngulfingState.ACTIVE,
        EngulfingState.FIRST_TEST,
        EngulfingState.PARTIAL_RETRACE,
        EngulfingState.CONTINUATION_CONFIRMED,
        EngulfingState.WEAKENED,
    }

    fvg_records = [
        engine.active_bullish_fvg,
        engine.active_bearish_fvg,
        *engine.completed_fvg,
    ]
    seen: set[tuple[str, int, int]] = set()
    for record in fvg_records:
        if record is None:
            continue
        key = ("FVG", int(record.formation_index), int(record.direction))
        if key in seen:
            continue
        seen.add(key)
        identity = f"FVG:{timeframe}:{record.formation_index}:{int(record.direction)}"
        confirmed_at, available_at = _metadata(confirmations, identity, fallback=record.formation_time)
        low = float(record.lower_boundary)
        high = float(record.upper_boundary)
        out.append(
            TargetEvidence(
                uid=f"TE-{_uid(symbol, identity)}",
                symbol=symbol,
                timeframe=timeframe,
                evidence_type=TargetEvidenceType.FVG,
                family=TargetEvidenceFamily.IMBALANCE,
                roles=(TargetRole.IMBALANCE,),
                low=low,
                high=high,
                anchor_price=(low + high) / 2.0,
                origin_index=int(record.formation_index),
                origin_time=record.formation_time,
                confirmed_at=confirmed_at,
                available_at=available_at,
                source_state=record.state.name,
                target_eligible=record.state in fvg_eligible,
                native_origin_id=identity,
                origin_event_id=identity,
                source_identity=identity,
                formation_atr=float(record.formation_atr),
                source_quality=float(record.quality),
            )
        )

    engulf_records = [
        engine.active_bullish_engulfing,
        engine.active_bearish_engulfing,
        *engine.completed_engulfing,
    ]
    for record in engulf_records:
        if record is None:
            continue
        key = ("ENGULF", int(record.formation_index), int(record.direction))
        if key in seen:
            continue
        seen.add(key)
        identity = f"ENG:{timeframe}:{record.formation_index}:{int(record.direction)}"
        confirmed_at, available_at = _metadata(confirmations, identity, fallback=record.formation_time)
        low = float(record.lower_boundary)
        high = float(record.upper_boundary)
        out.append(
            TargetEvidence(
                uid=f"TE-{_uid(symbol, identity)}",
                symbol=symbol,
                timeframe=timeframe,
                evidence_type=TargetEvidenceType.ENGULFING,
                family=TargetEvidenceFamily.REACTION,
                roles=(TargetRole.REACTION,),
                low=low,
                high=high,
                anchor_price=(low + high) / 2.0,
                origin_index=int(record.formation_index),
                origin_time=record.formation_time,
                confirmed_at=confirmed_at,
                available_at=available_at,
                source_state=record.state.name,
                target_eligible=record.state in engulf_eligible,
                native_origin_id=identity,
                origin_event_id=identity,
                source_identity=identity,
                formation_atr=float(getattr(record, "body_atr", 0.0) or 0.0),
                source_quality=float(record.quality),
            )
        )
    return tuple(out)


__all__ = [
    "fvg_engulfing_evidence",
    "liquidity_evidence",
    "order_block_evidence",
]
