from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha1
from typing import Iterable

import pandas as pd

from .models import (
    LiquidityScope,
    TargetCluster,
    TargetClusterKind,
    TargetClusterQuality,
    TargetEvidence,
    TargetEvidenceType,
    TargetSide,
    TargetingSnapshot,
)


@dataclass(frozen=True, slots=True)
class TargetClusterConfig:
    evidence_gap_atr: float = 0.25
    max_span_atr: float = 0.75
    origin_bar_tolerance: int = 2
    origin_price_tolerance_atr: float = 0.25
    origin_max_span_atr: float = 0.75

    def __post_init__(self) -> None:
        if self.evidence_gap_atr < 0:
            raise ValueError("evidence_gap_atr must be >= 0")
        if self.max_span_atr <= 0:
            raise ValueError("max_span_atr must be > 0")
        if self.origin_bar_tolerance < 0:
            raise ValueError("origin_bar_tolerance must be >= 0")
        if self.origin_price_tolerance_atr < 0:
            raise ValueError("origin_price_tolerance_atr must be >= 0")
        if self.origin_max_span_atr <= 0:
            raise ValueError("origin_max_span_atr must be > 0")


def _interval_gap(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
    if a_high < b_low:
        return b_low - a_high
    if b_high < a_low:
        return a_low - b_high
    return 0.0


def _origin_group(item: TargetEvidence) -> str | None:
    if item.evidence_type in {
        TargetEvidenceType.FVG,
        TargetEvidenceType.ORDER_BLOCK,
        TargetEvidenceType.ENGULFING,
    }:
        return "IMPULSE"
    if item.evidence_type in {
        TargetEvidenceType.LIQUIDITY,
        TargetEvidenceType.SUPPORT_RESISTANCE,
    }:
        return "STRUCTURAL"
    return None


def deduplicate_origin_events(
    evidence: Iterable[TargetEvidence],
    *,
    reference_atr: float,
    config: TargetClusterConfig | None = None,
) -> tuple[TargetEvidence, ...]:
    """Collapse obviously correlated facts without deleting source evidence.

    Two conservative origin groups are allowed:
    - IMPULSE: FVG / Order Block / Engulfing from the same local impulse;
    - STRUCTURAL: Liquidity / S-R derived from the same local structural price area.

    Cross-group facts are never collapsed. Same-timeframe, origin-bar proximity,
    interval proximity and a maximum price diameter are all required. The evidence
    objects remain separate; only ``origin_event_id`` is shared for independent-count
    purposes. Singleton evidence keeps its native origin-event identity.
    """
    cfg = config or TargetClusterConfig()
    atr = max(float(reference_atr), 1e-12)
    items = list(evidence)
    grouped_indices: set[int] = set()
    replacements: dict[int, str] = {}

    order = sorted(
        range(len(items)),
        key=lambda idx: (
            items[idx].timeframe,
            pd.Timestamp(items[idx].confirmed_at),
            items[idx].origin_index,
            items[idx].uid,
        ),
    )
    for seed_idx in order:
        if seed_idx in grouped_indices:
            continue
        seed = items[seed_idx]
        group_kind = _origin_group(seed)
        if group_kind is None:
            continue
        group = [seed_idx]
        grouped_indices.add(seed_idx)
        min_origin = max_origin = seed.origin_index
        low = seed.low
        high = seed.high
        for candidate_idx in order:
            if candidate_idx in grouped_indices:
                continue
            candidate = items[candidate_idx]
            if candidate.timeframe != seed.timeframe or _origin_group(candidate) != group_kind:
                continue
            next_min_origin = min(min_origin, candidate.origin_index)
            next_max_origin = max(max_origin, candidate.origin_index)
            if next_max_origin - next_min_origin > cfg.origin_bar_tolerance:
                continue
            if _interval_gap(low, high, candidate.low, candidate.high) / atr > cfg.origin_price_tolerance_atr:
                continue
            next_low = min(low, candidate.low)
            next_high = max(high, candidate.high)
            if (next_high - next_low) / atr > cfg.origin_max_span_atr:
                continue
            group.append(candidate_idx)
            grouped_indices.add(candidate_idx)
            min_origin = next_min_origin
            max_origin = next_max_origin
            low = next_low
            high = next_high

        if len(group) <= 1:
            continue
        anchor_idx = min(
            group,
            key=lambda idx: (pd.Timestamp(items[idx].confirmed_at), items[idx].uid),
        )
        event_id = f"EVT:{group_kind}:{seed.timeframe}:{items[anchor_idx].native_origin_id}"
        for idx in group:
            replacements[idx] = event_id

    return tuple(
        replace(item, origin_event_id=replacements.get(idx, item.origin_event_id))
        for idx, item in enumerate(items)
    )


def _side(item: TargetEvidence, current_price: float) -> TargetSide:
    if item.low > current_price:
        return TargetSide.ABOVE
    if item.high < current_price:
        return TargetSide.BELOW
    return TargetSide.AT_PRICE


def _quality(independent_origins: int) -> TargetClusterQuality:
    if independent_origins <= 1:
        return TargetClusterQuality.SINGLE
    if independent_origins == 2:
        return TargetClusterQuality.SUPPORTED
    if independent_origins == 3:
        return TargetClusterQuality.MULTI_EVIDENCE
    return TargetClusterQuality.DENSE


def _core_zone(items: list[TargetEvidence]) -> tuple[float | None, float | None]:
    points = sorted({value for item in items for value in (item.low, item.high)})
    if not points:
        return None, None
    scored: list[tuple[int, float]] = []
    for point in points:
        origins = {
            item.origin_event_id
            for item in items
            if item.low <= point <= item.high
        }
        scored.append((len(origins), point))
    maximum = max(score for score, _ in scored)
    if maximum <= 1 and len(items) > 1:
        return None, None
    winners = [point for score, point in scored if score == maximum]
    return min(winners), max(winners)


def _cluster_identity(items: list[TargetEvidence], side: TargetSide) -> str:
    raw = "|".join(sorted(item.uid for item in items))
    digest = sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"TC-{side.value}-{digest}"


def _build_cluster(
    items: list[TargetEvidence],
    *,
    current_price: float,
    reference_atr: float,
) -> TargetCluster:
    side = _side(items[0], current_price)
    low = min(item.low for item in items)
    high = max(item.high for item in items)
    core_low, core_high = _core_zone(items)
    liquidity = [item for item in items if item.is_liquidity]
    if side is TargetSide.ABOVE:
        liquidity_anchor = min((item.anchor_price for item in liquidity if item.anchor_price is not None), default=None)
        distance_price = max(0.0, low - current_price)
    elif side is TargetSide.BELOW:
        liquidity_anchor = max((item.anchor_price for item in liquidity if item.anchor_price is not None), default=None)
        distance_price = max(0.0, current_price - high)
    else:
        liquidity_anchor = min(
            (item.anchor_price for item in liquidity if item.anchor_price is not None),
            key=lambda price: abs(float(price) - current_price),
            default=None,
        )
        distance_price = 0.0
    origins = {item.origin_event_id for item in items}
    families = {item.family for item in items}
    timeframes = tuple(sorted({item.timeframe for item in items}))
    roles = tuple(sorted({role for item in items for role in item.roles}, key=lambda role: role.value))
    kind = TargetClusterKind.LIQUIDITY_TARGET if liquidity else TargetClusterKind.TECHNICAL_ZONE
    return TargetCluster(
        identity=_cluster_identity(items, side),
        side=side,
        kind=kind,
        envelope_low=low,
        envelope_high=high,
        core_low=core_low,
        core_high=core_high,
        liquidity_anchor=None if liquidity_anchor is None else float(liquidity_anchor),
        distance_price=float(distance_price),
        distance_percent=(float(distance_price) / abs(current_price) * 100.0) if current_price else 0.0,
        distance_atr=float(distance_price) / max(float(reference_atr), 1e-12),
        evidence=tuple(sorted(items, key=lambda item: (item.low, item.high, item.uid))),
        raw_source_count=len(items),
        independent_origin_count=len(origins),
        independent_family_count=len(families),
        timeframes_present=timeframes,
        roles_present=roles,
        quality=_quality(len(origins)),
    )


def cluster_target_evidence(
    evidence: Iterable[TargetEvidence],
    *,
    current_price: float,
    reference_atr: float,
    config: TargetClusterConfig | None = None,
) -> tuple[TargetCluster, ...]:
    cfg = config or TargetClusterConfig()
    atr = max(float(reference_atr), 1e-12)
    eligible = [item for item in evidence if item.target_eligible]
    result: list[TargetCluster] = []
    for side in (TargetSide.BELOW, TargetSide.AT_PRICE, TargetSide.ABOVE):
        side_items = sorted(
            (item for item in eligible if _side(item, current_price) is side),
            key=lambda item: (item.low, item.high, item.uid),
        )
        groups: list[list[TargetEvidence]] = []
        for item in side_items:
            chosen: list[TargetEvidence] | None = None
            for group in groups:
                low = min(member.low for member in group)
                high = max(member.high for member in group)
                gap = _interval_gap(low, high, item.low, item.high) / atr
                next_span = (max(high, item.high) - min(low, item.low)) / atr
                if gap <= cfg.evidence_gap_atr and next_span <= cfg.max_span_atr:
                    chosen = group
                    break
            if chosen is None:
                groups.append([item])
            else:
                chosen.append(item)
        result.extend(
            _build_cluster(group, current_price=current_price, reference_atr=atr)
            for group in groups
        )
    return tuple(sorted(result, key=lambda cluster: (cluster.side.value, cluster.distance_atr, cluster.identity)))


def _nearest(clusters: Iterable[TargetCluster], side: TargetSide) -> TargetCluster | None:
    candidates = [
        cluster
        for cluster in clusters
        if cluster.side is side and cluster.kind is TargetClusterKind.LIQUIDITY_TARGET
    ]
    return min(candidates, key=lambda cluster: (cluster.distance_atr, cluster.identity), default=None)


def _highest_confluence(clusters: Iterable[TargetCluster], side: TargetSide) -> TargetCluster | None:
    candidates = [
        cluster
        for cluster in clusters
        if cluster.side is side and cluster.kind is TargetClusterKind.LIQUIDITY_TARGET
    ]
    return max(
        candidates,
        key=lambda cluster: (
            cluster.independent_origin_count,
            cluster.independent_family_count,
            -cluster.distance_atr,
            cluster.identity,
        ),
        default=None,
    )


def _nearest_liquidity(
    evidence: Iterable[TargetEvidence],
    *,
    current_price: float,
    side: TargetSide,
    scope: LiquidityScope,
) -> TargetEvidence | None:
    candidates = [
        item
        for item in evidence
        if item.target_eligible
        and item.evidence_type is TargetEvidenceType.LIQUIDITY
        and item.liquidity_scope is scope
        and _side(item, current_price) is side
    ]
    return min(
        candidates,
        key=lambda item: (abs(float(item.anchor_price or item.midpoint) - current_price), item.uid),
        default=None,
    )


def build_targeting_snapshot(
    *,
    symbol: str,
    as_of,
    current_price: float,
    reference_timeframe: str,
    reference_atr: float,
    evidence: Iterable[TargetEvidence],
    config: TargetClusterConfig | None = None,
    evidence_pre_deduplicated: bool = False,
) -> TargetingSnapshot:
    """Build one causal targeting snapshot.

    ``evidence_pre_deduplicated`` is an execution optimization only. Callers may set
    it to ``True`` only when the provided evidence has already passed through the
    canonical origin-event de-duplication rule for the same reference ATR/config.
    The causal availability filter still runs in either mode.
    """

    cfg = config or TargetClusterConfig()
    cutoff = pd.Timestamp(as_of)
    causal_evidence = tuple(
        item
        for item in evidence
        if pd.Timestamp(item.available_at) <= cutoff
    )
    deduped = (
        causal_evidence
        if evidence_pre_deduplicated
        else deduplicate_origin_events(
            causal_evidence,
            reference_atr=reference_atr,
            config=cfg,
        )
    )
    clusters = cluster_target_evidence(
        deduped,
        current_price=current_price,
        reference_atr=reference_atr,
        config=cfg,
    )
    return TargetingSnapshot(
        symbol=symbol,
        as_of=as_of,
        current_price=float(current_price),
        reference_timeframe=reference_timeframe,
        reference_atr=float(reference_atr),
        clusters=clusters,
        nearest_upside_target=_nearest(clusters, TargetSide.ABOVE),
        nearest_downside_target=_nearest(clusters, TargetSide.BELOW),
        highest_confluence_upside=_highest_confluence(clusters, TargetSide.ABOVE),
        highest_confluence_downside=_highest_confluence(clusters, TargetSide.BELOW),
        nearest_internal_upside_liquidity=_nearest_liquidity(
            deduped, current_price=current_price, side=TargetSide.ABOVE, scope=LiquidityScope.INTERNAL
        ),
        nearest_internal_downside_liquidity=_nearest_liquidity(
            deduped, current_price=current_price, side=TargetSide.BELOW, scope=LiquidityScope.INTERNAL
        ),
        nearest_external_upside_liquidity=_nearest_liquidity(
            deduped, current_price=current_price, side=TargetSide.ABOVE, scope=LiquidityScope.EXTERNAL
        ),
        nearest_external_downside_liquidity=_nearest_liquidity(
            deduped, current_price=current_price, side=TargetSide.BELOW, scope=LiquidityScope.EXTERNAL
        ),
    )


__all__ = [
    "TargetClusterConfig",
    "build_targeting_snapshot",
    "cluster_target_evidence",
    "deduplicate_origin_events",
]
