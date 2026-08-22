from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.structure_location_replay import (
    CachedStructureLocationMTFRunner,
    CausalBarClock,
)
from financial_dashboard.target_evidence_replay import (
    FvgEngulfingMTFReplayRunner,
    LiquidityMTFReplayRunner,
    OrderBlockMTFReplayRunner,
)
from financial_dashboard.targeting.adapters import support_resistance_evidence
from financial_dashboard.targeting.causal_inputs import (
    CausalInputUnavailableError,
    clip_analysis_inputs_at_cutoff,
)
from financial_dashboard.targeting.clustering import build_targeting_snapshot
from financial_dashboard.targeting.enrichment import enrich_liquidity_scope
from financial_dashboard.targeting.models import TargetCluster, TargetingSnapshot
from financial_dashboard.targeting.proximity import wilder_atr


@dataclass(frozen=True, slots=True)
class TargetingReplayPoint:
    reference_index: int
    reference_timestamp: Any
    available_at: Any
    snapshot: TargetingSnapshot


@dataclass(frozen=True, slots=True)
class TargetingTransition:
    available_at: Any
    field: str
    previous_identity: str | None
    new_identity: str | None
    previous_distance_atr: float | None
    new_distance_atr: float | None


@dataclass(frozen=True, slots=True)
class TargetingHistoricalReplay:
    symbol: str
    timeframes: tuple[str, ...]
    reference_timeframe: str
    points: tuple[TargetingReplayPoint, ...]
    transitions: tuple[TargetingTransition, ...]

    @property
    def latest(self) -> TargetingSnapshot | None:
        return None if not self.points else self.points[-1].snapshot


def _cluster_identity(cluster: TargetCluster | None) -> tuple[str | None, float | None]:
    if cluster is None:
        return None, None
    return cluster.identity, float(cluster.distance_atr)


def _transition_ledger(points: tuple[TargetingReplayPoint, ...]) -> tuple[TargetingTransition, ...]:
    fields = (
        "nearest_upside_target",
        "nearest_downside_target",
        "highest_confluence_upside",
        "highest_confluence_downside",
    )
    out: list[TargetingTransition] = []
    previous: dict[str, tuple[str | None, float | None]] = {
        field: (None, None) for field in fields
    }
    initialized = False
    for point in points:
        current = {
            field: _cluster_identity(getattr(point.snapshot, field))
            for field in fields
        }
        if not initialized:
            previous = current
            initialized = True
            continue
        for field in fields:
            previous_identity, previous_distance = previous[field]
            new_identity, new_distance = current[field]
            if previous_identity == new_identity:
                continue
            out.append(
                TargetingTransition(
                    available_at=point.available_at,
                    field=field,
                    previous_identity=previous_identity,
                    new_identity=new_identity,
                    previous_distance_atr=previous_distance,
                    new_distance_atr=new_distance,
                )
            )
        previous = current
    return tuple(out)


class TargetingHistoricalReplayRunner:
    """Replay descriptive targeting through time using causal input prefixes.

    Every replay point reruns the source engines on bars that were actually
    available at that reference instant. This is intentionally more expensive than
    the live/latest workspace path, but it makes historical validation resistant to
    future-tail state mutation and therefore suitable for no-lookahead audits.
    """

    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        clock: CausalBarClock | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or CausalBarClock()

    def replay(
        self,
        symbol: str,
        *,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        reference_timeframe: str = "1h",
        minimum_bars_per_timeframe: int = 20,
        step: int = 1,
        max_points: int | None = None,
    ) -> TargetingHistoricalReplay:
        if step < 1:
            raise ValueError("step must be >= 1")
        if max_points is not None and max_points < 1:
            raise ValueError("max_points must be >= 1 when provided")

        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = normalize_timeframes(
            timeframes,
            supported=ANALYSIS_TIMEFRAMES,
            label="targeting historical replay",
        )
        reference = reference_timeframe.strip().lower()
        if reference not in normalized_timeframes:
            raise ValueError("reference_timeframe must be included in timeframes")

        inputs = load_analysis_inputs(
            self.store,
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
        )
        reference_frame = inputs.for_timeframe(reference).input_batch.frame
        candidate_indices = list(range(len(reference_frame)))[::step]
        if max_points is not None:
            candidate_indices = candidate_indices[-max_points:]

        points: list[TargetingReplayPoint] = []
        for reference_index in candidate_indices:
            reference_row = reference_frame.iloc[reference_index]
            reference_timestamp = reference_row["timestamp"]
            cutoff = self.clock.available_at(reference_timestamp, reference)
            try:
                clipped = clip_analysis_inputs_at_cutoff(
                    inputs,
                    cutoff=cutoff,
                    clock=self.clock,
                    minimum_bars_per_timeframe=minimum_bars_per_timeframe,
                )
            except CausalInputUnavailableError:
                continue

            structure = CachedStructureLocationMTFRunner(
                self.store,
                clock=self.clock,
            ).run(
                symbol=normalized_symbol,
                timeframes=normalized_timeframes,
                input_snapshot=clipped,
            )
            liquidity = LiquidityMTFReplayRunner(
                self.store,
                clock=self.clock,
            ).replay(
                normalized_symbol,
                timeframes=normalized_timeframes,
                input_snapshot=clipped,
            )
            order_block = OrderBlockMTFReplayRunner(
                self.store,
                clock=self.clock,
            ).replay(
                normalized_symbol,
                timeframes=normalized_timeframes,
                input_snapshot=clipped,
            )
            fvg_engulfing = FvgEngulfingMTFReplayRunner(
                self.store,
                clock=self.clock,
            ).replay(
                normalized_symbol,
                timeframes=normalized_timeframes,
                input_snapshot=clipped,
            )

            evidence = []
            structure_by_timeframe = {
                timeframe: structure.replay_for(timeframe).market_structure
                for timeframe in normalized_timeframes
            }
            atr_by_timeframe = {
                timeframe: liquidity.for_timeframe(timeframe).atr
                for timeframe in liquidity.timeframes
            }
            evidence.extend(
                enrich_liquidity_scope(
                    liquidity.evidence,
                    structure_by_timeframe=structure_by_timeframe,
                    atr_by_timeframe=atr_by_timeframe,
                )
            )
            for timeframe in normalized_timeframes:
                structure_replay = structure.replay_for(timeframe)
                evidence.extend(
                    support_resistance_evidence(
                        symbol=normalized_symbol,
                        timeframe=timeframe,
                        snapshot=structure_replay.support_resistance,
                        clock=self.clock,
                    )
                )
            evidence.extend(order_block.evidence)
            evidence.extend(fvg_engulfing.evidence)

            reference_prefix = clipped.for_timeframe(reference).input_batch.frame
            current_price = float(reference_prefix.iloc[-1]["close"])
            snapshot = build_targeting_snapshot(
                symbol=normalized_symbol,
                as_of=cutoff,
                current_price=current_price,
                reference_timeframe=reference,
                reference_atr=wilder_atr(reference_prefix),
                evidence=evidence,
            )
            points.append(
                TargetingReplayPoint(
                    reference_index=int(reference_index),
                    reference_timestamp=reference_timestamp,
                    available_at=cutoff,
                    snapshot=snapshot,
                )
            )

        ordered_points = tuple(points)
        return TargetingHistoricalReplay(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            reference_timeframe=reference,
            points=ordered_points,
            transitions=_transition_ledger(ordered_points),
        )


def snapshot_signature(snapshot: TargetingSnapshot | None) -> tuple:
    """Stable replay signature for regression/future-tail comparisons."""

    if snapshot is None:
        return ()
    clusters = tuple(
        (
            cluster.identity,
            cluster.side.value,
            cluster.kind.value,
            round(cluster.envelope_low, 10),
            round(cluster.envelope_high, 10),
            round(cluster.distance_atr, 10),
            cluster.independent_origin_count,
            cluster.independent_family_count,
            tuple(item.uid for item in cluster.evidence),
        )
        for cluster in snapshot.clusters
    )
    return (
        str(pd.Timestamp(snapshot.as_of)),
        round(snapshot.current_price, 10),
        round(snapshot.reference_atr, 10),
        clusters,
        None if snapshot.nearest_upside_target is None else snapshot.nearest_upside_target.identity,
        None if snapshot.nearest_downside_target is None else snapshot.nearest_downside_target.identity,
        None if snapshot.highest_confluence_upside is None else snapshot.highest_confluence_upside.identity,
        None if snapshot.highest_confluence_downside is None else snapshot.highest_confluence_downside.identity,
    )


__all__ = [
    "TargetingHistoricalReplay",
    "TargetingHistoricalReplayRunner",
    "TargetingReplayPoint",
    "TargetingTransition",
    "snapshot_signature",
]
