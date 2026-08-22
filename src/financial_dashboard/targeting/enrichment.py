from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping

from financial_dashboard.mtf_replay import MarketStructureTimeframeSnapshot

from .models import LiquidityScope, TargetEvidence, TargetEvidenceType


def _levels(snapshot: MarketStructureTimeframeSnapshot, prefix: str) -> tuple[float, ...]:
    export = snapshot.export
    if export is None:
        return ()
    names = (
        f"{prefix}_protected_low",
        f"{prefix}_protected_high",
        f"{prefix}_weak_low",
        f"{prefix}_weak_high",
    )
    return tuple(
        float(value)
        for name in names
        if (value := getattr(export, name, None)) is not None
    )


def enrich_liquidity_scope(
    evidence: Iterable[TargetEvidence],
    *,
    structure_by_timeframe: Mapping[str, MarketStructureTimeframeSnapshot],
    atr_by_timeframe: Mapping[str, float],
    tolerance_atr: float = 0.25,
) -> tuple[TargetEvidence, ...]:
    """Classify liquidity only against authoritative structure on the same timeframe.

    This deliberately forbids lower-timeframe promotion. If a liquidity pool does not
    geometrically match a confirmed internal/external structure role, it remains
    UNCLASSIFIED rather than guessing from trend or timeframe rank.
    """
    if tolerance_atr < 0:
        raise ValueError("tolerance_atr must be >= 0")
    out: list[TargetEvidence] = []
    for item in evidence:
        if item.evidence_type is not TargetEvidenceType.LIQUIDITY:
            out.append(item)
            continue
        snapshot = structure_by_timeframe.get(item.timeframe)
        atr = max(float(atr_by_timeframe.get(item.timeframe, 0.0)), 1e-12)
        anchor = float(item.anchor_price if item.anchor_price is not None else item.midpoint)
        scope = LiquidityScope.UNCLASSIFIED
        if snapshot is not None:
            external = _levels(snapshot, "external")
            internal = _levels(snapshot, "internal")
            threshold = atr * tolerance_atr
            if any(abs(anchor - level) <= threshold for level in external):
                scope = LiquidityScope.EXTERNAL
            elif any(abs(anchor - level) <= threshold for level in internal):
                scope = LiquidityScope.INTERNAL
        out.append(replace(item, liquidity_scope=scope))
    return tuple(out)


__all__ = ["enrich_liquidity_scope"]
