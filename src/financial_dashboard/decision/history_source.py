from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.context.builder import CrossDomainBuildInputs, build_cross_domain_context
from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.engine_input import EngineInputBatch
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision_input import DecisionInputSnapshot, build_decision_input_snapshot
from financial_dashboard.engines.fvg_engulfing import FvgEngulfingEngine
from financial_dashboard.engines.fvg_engulfing_models import FvgEngulfingConfig, SUPPORTED_TIMEFRAMES
from financial_dashboard.engines.liquidity_engine import LiquidityEngine
from financial_dashboard.engines.liquidity_models import LiquidityConfig, LiquidityPoolState
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine
from financial_dashboard.engines.order_block import OrderBlockEngine
from financial_dashboard.engines.order_block_behavior import OrderBlockBehaviorTracker
from financial_dashboard.engines.order_block_engine import OrderBlockConfig
from financial_dashboard.engines.pattern_compression_core import PatternCompressionConfig
from financial_dashboard.engines.pattern_compression_runtime_engine import RuntimePatternCompressionEngine
from financial_dashboard.engines.stabil_support_behavior import (
    StabilSupportBehaviorConfig,
    build_support_behavior,
)
from financial_dashboard.engines.stabil_support_lifecycle import (
    build_daily_support_observations,
    build_support_lifecycle,
)
from financial_dashboard.engines.stabil_trend_engine import StabilTrendConfig
from financial_dashboard.engines.support_resistance_runtime_engine import RuntimeSupportResistanceRangeEngine
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.structure_location_replay import (
    CausalBarClock,
    StructureLocationMTFResult,
    StructureLocationTimeframeReplay,
    _support_snapshot,
)
from financial_dashboard.mtf_replay import market_structure_timeframe_snapshot
from financial_dashboard.target_evidence_replay import (
    FvgEngulfingMTFReplayRunner,
    TargetEvidenceMTFReplay,
)
from financial_dashboard.targeting.adapters import (
    fvg_engulfing_evidence,
    liquidity_evidence,
    order_block_evidence,
    support_resistance_evidence,
)
from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.clustering import build_targeting_snapshot, deduplicate_origin_events
from financial_dashboard.targeting.enrichment import enrich_liquidity_scope
from financial_dashboard.targeting.models import TargetEvidenceSnapshot
from financial_dashboard.three_domain_replay import PatternTimeframeSnapshot
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner
from financial_dashboard.volatility_mtf_replay import VolatilityMTFReplayRunner, VOLATILITY_TIMEFRAMES


@dataclass(frozen=True, slots=True)
class HistoricalDecisionInputConfig:
    """Select the causal 1h decision points to materialize from one native replay pass."""

    decision_timeframe: str = "1h"
    pattern_profile: str | None = None
    max_bars: int | None = None
    start_at: Any | None = None
    end_at: Any | None = None

    def __post_init__(self) -> None:
        if self.decision_timeframe.strip().lower() != "1h":
            raise ValueError("v1 historical decision input source evaluates 1h closes")
        if self.max_bars is not None and self.max_bars < 1:
            raise ValueError("max_bars must be >= 1 when provided")


@dataclass(frozen=True, slots=True)
class HistoricalDecisionInputReplay:
    symbol: str
    decision_timeframe: str
    cutoffs: tuple[Any, ...]
    snapshots: tuple[DecisionInputSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _StructureCapture:
    index: int
    market: Any
    support: Any


@dataclass(frozen=True, slots=True)
class _TargetCapture:
    index: int
    snapshot: TargetEvidenceSnapshot
    evidence: tuple[Any, ...]
    liquidity_behavior: Any | None = None
    order_block_behavior: tuple[Any, ...] = ()
    fvg_lifecycle: tuple[Any, ...] = ()
    engulfing_lifecycle: tuple[Any, ...] = ()


def _wilder_atr_history(frame: pd.DataFrame, length: int = 14) -> tuple[float, ...]:
    tr_values: list[float] = []
    values: list[float] = []
    prev_close: float | None = None
    atr: float | None = None
    for row in frame.itertuples(index=False):
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        tr = high - low
        if prev_close is not None:
            tr = max(tr, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)
        if len(tr_values) == length:
            atr = sum(tr_values[-length:]) / length
        elif len(tr_values) > length:
            assert atr is not None
            atr = (atr * (length - 1) + tr) / length
        values.append(max(float(atr if atr is not None else tr), 1e-12))
        prev_close = close
    return tuple(values)


def _availability_ns(frame: pd.DataFrame, timeframe: str, clock: CausalBarClock) -> tuple[int, ...]:
    values = tuple(pd.Timestamp(clock.available_at(value, timeframe)).value for value in frame["timestamp"])
    if any(left > right for left, right in zip(values, values[1:])):
        raise ValueError(f"non-monotonic causal availability for {timeframe}")
    return values


def _capture_indices(
    inputs: AnalysisInputSnapshot,
    *,
    cutoffs: tuple[pd.Timestamp, ...],
    clock: CausalBarClock,
) -> dict[str, tuple[int, ...]]:
    result: dict[str, tuple[int, ...]] = {}
    for timeframe in inputs.timeframes:
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        available_ns = _availability_ns(frame, timeframe, clock)
        indices: list[int] = []
        for cutoff in cutoffs:
            index = bisect_right(available_ns, cutoff.value) - 1
            if index < 0:
                raise ValueError(
                    f"decision cutoff {cutoff} precedes first causal {timeframe} bar"
                )
            indices.append(index)
        result[timeframe] = tuple(indices)
    return result


def _prefix_batch(batch: EngineInputBatch, index: int) -> EngineInputBatch:
    return replace(batch, frame=batch.frame.iloc[: index + 1])


def _pattern_snapshot(
    *,
    symbol: str,
    timeframe: str,
    bar_count: int,
    engine: RuntimePatternCompressionEngine,
) -> PatternTimeframeSnapshot:
    result = engine.snapshot()
    export = engine.export_contract
    candidate = getattr(engine, "active_candidate", None)
    candidate_valid = candidate is not None and bool(getattr(candidate, "valid", False))
    return PatternTimeframeSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        as_of=None if result is None else result.timestamp,
        bar_count=bar_count,
        result=result,
        export=export,
        native_state=getattr(engine, "pattern_state", None),
        active_start_bar=getattr(candidate, "start_bar", None) if candidate_valid else None,
        active_known_bar=getattr(candidate, "known_bar", None) if candidate_valid else None,
        progress=getattr(candidate, "progress", None) if candidate_valid else None,
        contraction=getattr(candidate, "contraction", None) if candidate_valid else None,
        raw_quality=getattr(candidate, "raw_quality", None) if candidate_valid else None,
        selection_score=getattr(candidate, "selection_score", None) if candidate_valid else None,
        upper_touches=int(getattr(candidate, "upper_touches", 0)) if candidate_valid else 0,
        lower_touches=int(getattr(candidate, "lower_touches", 0)) if candidate_valid else 0,
        quality_frozen=bool(getattr(candidate, "quality_frozen", False)) if candidate_valid else False,
    )


def _build_structure_pattern_captures(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    capture_indices: Mapping[str, tuple[int, ...]],
    clock: CausalBarClock,
    pattern_profile: str | None,
) -> tuple[
    dict[str, dict[int, _StructureCapture]],
    dict[str, dict[int, PatternTimeframeSnapshot]],
    StructureLocationMTFResult,
]:
    structure_captures: dict[str, dict[int, _StructureCapture]] = {}
    pattern_captures: dict[str, dict[int, PatternTimeframeSnapshot]] = {}
    final_replays: dict[str, StructureLocationTimeframeReplay] = {}

    pattern_config = None if pattern_profile is None else PatternCompressionConfig(profile=pattern_profile)

    for timeframe in inputs.timeframes:
        batch = inputs.for_timeframe(timeframe).input_batch
        wanted = set(capture_indices[timeframe])
        market_engine = MarketStructureEngine()
        support_engine = RuntimeSupportResistanceRangeEngine()
        pattern_engine = RuntimePatternCompressionEngine(pattern_config)
        tf_structure: dict[int, _StructureCapture] = {}
        tf_pattern: dict[int, PatternTimeframeSnapshot] = {}

        for index, (_, bar) in enumerate(batch.frame.iterrows()):
            support_engine.update(bar)
            market_engine.update(bar)
            pattern_engine.update(bar)
            if index not in wanted:
                continue
            prefix = _prefix_batch(batch, index)
            market = market_structure_timeframe_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                batch=prefix,
                engine=market_engine,
            )
            support = _support_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                batch=prefix,
                engine=support_engine,
                clock=clock,
            )
            tf_structure[index] = _StructureCapture(index=index, market=market, support=support)
            tf_pattern[index] = _pattern_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                bar_count=index + 1,
                engine=pattern_engine,
            )

        final_market = market_structure_timeframe_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            batch=batch,
            engine=market_engine,
        )
        final_support = _support_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            batch=batch,
            engine=support_engine,
            clock=clock,
        )
        final_replays[timeframe] = StructureLocationTimeframeReplay(
            timeframe=timeframe,
            input_batch=batch,
            market_structure=final_market,
            support_resistance=final_support,
        )
        structure_captures[timeframe] = tf_structure
        pattern_captures[timeframe] = tf_pattern

    full_structure = StructureLocationMTFResult(
        symbol=symbol,
        timeframes=tuple(inputs.timeframes),
        replays=final_replays,
        confluence=(),
        location_outcomes=(),
        event_zone_links=(),
    )
    return structure_captures, pattern_captures, full_structure


def _build_liquidity_captures(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    capture_indices: Mapping[str, tuple[int, ...]],
    clock: CausalBarClock,
) -> dict[str, dict[int, _TargetCapture]]:
    config = LiquidityConfig()
    out: dict[str, dict[int, _TargetCapture]] = {}
    for timeframe in inputs.timeframes:
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        wanted = set(capture_indices[timeframe])
        atrs = _wilder_atr_history(frame, config.atr_length)
        engine = LiquidityEngine(config)
        confirmations: dict[str, tuple[object, object]] = {}
        captured: dict[int, _TargetCapture] = {}
        for index, row in enumerate(frame.to_dict("records")):
            engine.update(row)
            for pool in engine.pools:
                if pool.identity not in confirmations and pool.state in {
                    LiquidityPoolState.ACTIVE,
                    LiquidityPoolState.TESTED,
                }:
                    confirmed_at = row["timestamp"]
                    confirmations[pool.identity] = (
                        confirmed_at,
                        clock.available_at(confirmed_at, timeframe),
                    )
            if index not in wanted:
                continue
            evidence = liquidity_evidence(
                symbol=symbol,
                timeframe=timeframe,
                engine=engine,
                confirmations=confirmations,
            )
            snapshot = TargetEvidenceSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                as_of=row["timestamp"],
                available_at=clock.available_at(row["timestamp"], timeframe),
                current_price=float(row["close"]),
                atr=atrs[index],
                evidence=evidence,
            )
            captured[index] = _TargetCapture(
                index=index,
                snapshot=snapshot,
                evidence=evidence,
                liquidity_behavior=engine.behavior_snapshot,
            )
        out[timeframe] = captured
    return out


def _build_order_block_captures(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    capture_indices: Mapping[str, tuple[int, ...]],
    clock: CausalBarClock,
) -> dict[str, dict[int, _TargetCapture]]:
    config = OrderBlockConfig()
    out: dict[str, dict[int, _TargetCapture]] = {}
    for timeframe in inputs.timeframes:
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        wanted = set(capture_indices[timeframe])
        atrs = _wilder_atr_history(frame)
        engine = OrderBlockEngine(config)
        behavior = OrderBlockBehaviorTracker(config)
        confirmations: dict[str, tuple[object, object]] = {}
        captured: dict[int, _TargetCapture] = {}
        latest_behavior: tuple[Any, ...] = ()
        for index, row in enumerate(frame.to_dict("records")):
            engine.update(row)
            latest_behavior = behavior.update(
                engine.records,
                bar_index=index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for record in engine.records:
                identity = f"OB:{timeframe}:{record.source_index}:{1 if record.bullish else -1}"
                if identity not in confirmations and record.active:
                    confirmed_at = row["timestamp"]
                    confirmations[identity] = (
                        confirmed_at,
                        clock.available_at(confirmed_at, timeframe),
                    )
            if index not in wanted:
                continue
            evidence = order_block_evidence(
                symbol=symbol,
                timeframe=timeframe,
                engine=engine,
                confirmations=confirmations,
            )
            behavior_state = {item.identity: item.state.value for item in latest_behavior}
            prefix = f"OB:{timeframe}:"
            evidence = tuple(
                replace(
                    item,
                    source_state=behavior_state.get(
                        str(item.native_origin_id).replace(prefix, "OB:", 1),
                        item.source_state,
                    ),
                )
                for item in evidence
            )
            snapshot = TargetEvidenceSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                as_of=row["timestamp"],
                available_at=clock.available_at(row["timestamp"], timeframe),
                current_price=float(row["close"]),
                atr=atrs[index],
                evidence=evidence,
            )
            captured[index] = _TargetCapture(
                index=index,
                snapshot=snapshot,
                evidence=evidence,
                order_block_behavior=latest_behavior,
            )
        out[timeframe] = captured
    return out


def _build_fvg_captures(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    capture_indices: Mapping[str, tuple[int, ...]],
    clock: CausalBarClock,
) -> dict[str, dict[int, _TargetCapture]]:
    out: dict[str, dict[int, _TargetCapture]] = {}
    for timeframe in inputs.timeframes:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            continue
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        wanted = set(capture_indices[timeframe])
        atrs = _wilder_atr_history(frame)
        engine = FvgEngulfingEngine(FvgEngulfingConfig(timeframe=timeframe))
        confirmations: dict[str, tuple[object, object]] = {}
        captured: dict[int, _TargetCapture] = {}
        for index, row in enumerate(frame.to_dict("records")):
            engine.update(row)
            active_records = (
                engine.active_bullish_fvg,
                engine.active_bearish_fvg,
                engine.active_bullish_engulfing,
                engine.active_bearish_engulfing,
            )
            for record in active_records:
                if record is None:
                    continue
                prefix = "FVG" if hasattr(record, "gap_size") else "ENG"
                identity = f"{prefix}:{timeframe}:{record.formation_index}:{int(record.direction)}"
                if identity not in confirmations:
                    confirmed_at = row["timestamp"]
                    confirmations[identity] = (
                        confirmed_at,
                        clock.available_at(confirmed_at, timeframe),
                    )
            if index not in wanted:
                continue
            evidence = fvg_engulfing_evidence(
                symbol=symbol,
                timeframe=timeframe,
                engine=engine,
                confirmations=confirmations,
            )
            snapshot = TargetEvidenceSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                as_of=row["timestamp"],
                available_at=clock.available_at(row["timestamp"], timeframe),
                current_price=float(row["close"]),
                atr=atrs[index],
                evidence=evidence,
            )
            captured[index] = _TargetCapture(
                index=index,
                snapshot=snapshot,
                evidence=evidence,
                fvg_lifecycle=FvgEngulfingMTFReplayRunner._fvg_snapshots(timeframe, engine),
                engulfing_lifecycle=FvgEngulfingMTFReplayRunner._engulfing_snapshots(timeframe, engine),
            )
        out[timeframe] = captured
    return out


def _target_view(
    *,
    symbol: str,
    indices: Mapping[str, int],
    captures: Mapping[str, Mapping[int, _TargetCapture]],
    kind: str,
) -> TargetEvidenceMTFReplay | None:
    selected: dict[str, _TargetCapture] = {}
    for timeframe, index in indices.items():
        point = captures.get(timeframe, {}).get(index)
        if point is not None:
            selected[timeframe] = point
    if not selected:
        return None

    timeframes = tuple(tf for tf in ANALYSIS_TIMEFRAMES if tf in selected)
    snapshots = {tf: selected[tf].snapshot for tf in timeframes}
    evidence = tuple(item for tf in timeframes for item in selected[tf].evidence)
    liquidity_behavior = None
    order_block_behavior = None
    fvg_lifecycle = None
    engulfing_lifecycle = None
    if kind == "liquidity":
        liquidity_behavior = {tf: selected[tf].liquidity_behavior for tf in timeframes}
    elif kind == "order_block":
        order_block_behavior = {tf: selected[tf].order_block_behavior for tf in timeframes}
    elif kind == "fvg":
        fvg_lifecycle = {tf: selected[tf].fvg_lifecycle for tf in timeframes}
        engulfing_lifecycle = {tf: selected[tf].engulfing_lifecycle for tf in timeframes}

    return TargetEvidenceMTFReplay(
        symbol=symbol,
        timeframes=timeframes,
        snapshots=snapshots,
        evidence=evidence,
        liquidity_behavior=liquidity_behavior,
        order_block_behavior=order_block_behavior,
        fvg_lifecycle=fvg_lifecycle,
        engulfing_lifecycle=engulfing_lifecycle,
    )


def _structure_view(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    indices: Mapping[str, int],
    captures: Mapping[str, Mapping[int, _StructureCapture]],
) -> StructureLocationMTFResult:
    replays: dict[str, StructureLocationTimeframeReplay] = {}
    for timeframe in inputs.timeframes:
        index = indices[timeframe]
        point = captures[timeframe][index]
        replays[timeframe] = StructureLocationTimeframeReplay(
            timeframe=timeframe,
            input_batch=_prefix_batch(inputs.for_timeframe(timeframe).input_batch, index),
            market_structure=point.market,
            support_resistance=point.support,
        )
    return StructureLocationMTFResult(
        symbol=symbol,
        timeframes=tuple(inputs.timeframes),
        replays=replays,
        confluence=(),
        location_outcomes=(),
        event_zone_links=(),
    )


def _pattern_view(
    *,
    symbol: str,
    structure: StructureLocationMTFResult,
    indices: Mapping[str, int],
    captures: Mapping[str, Mapping[int, PatternTimeframeSnapshot]],
) -> Any:
    snapshots = tuple(captures[tf][indices[tf]] for tf in structure.timeframes)
    return SimpleNamespace(
        symbol=symbol,
        timeframes=tuple(structure.timeframes),
        structure_location=structure,
        pattern_snapshots=snapshots,
    )


def _volume_view(full: Any, indices: Mapping[str, int], cutoff: Any) -> Any:
    timeframe_replays = []
    for replay in full.timeframe_replays:
        index = indices[replay.timeframe]
        latest = replay.history[index]
        links = tuple(link for link in replay.event_links if link.assessed_at <= cutoff)
        timeframe_replays.append(
            SimpleNamespace(
                timeframe=replay.timeframe,
                latest=latest,
                event_links=links,
            )
        )
    return SimpleNamespace(
        symbol=full.symbol,
        timeframes=tuple(full.timeframes),
        timeframe_replays=tuple(timeframe_replays),
    )


def _ham_view(full: Any, indices: Mapping[str, int]) -> Any:
    timeframe_replays = tuple(
        SimpleNamespace(
            timeframe=replay.timeframe,
            latest=replay.history[indices[replay.timeframe]],
        )
        for replay in full.timeframe_replays
    )
    return SimpleNamespace(
        symbol=full.symbol,
        timeframes=tuple(full.timeframes),
        timeframe_replays=timeframe_replays,
    )


def _volatility_view(full: Any, indices: Mapping[str, int]) -> Any:
    selected = {
        timeframe: full.for_timeframe(timeframe).snapshots[indices[timeframe]]
        for timeframe in full.timeframes
    }
    rows = {
        timeframe: SimpleNamespace(latest=snapshot)
        for timeframe, snapshot in selected.items()
    }
    return SimpleNamespace(
        symbol=full.symbol,
        timeframes=tuple(full.timeframes),
        for_timeframe=lambda timeframe: rows[timeframe.strip().lower()],
    )


def _stabil_points(
    inputs: AnalysisInputSnapshot,
    *,
    indices_1d: tuple[int, ...],
) -> dict[int, Any]:
    batch = inputs.for_timeframe("1d").input_batch
    config = StabilTrendConfig()
    behavior_config = StabilSupportBehaviorConfig()
    observations = build_daily_support_observations(batch.frame, config=config)
    wanted = sorted(set(indices_1d))
    points: dict[int, Any] = {}
    for index in wanted:
        if index >= len(observations):
            raise ValueError("Stabil observation history is shorter than 1d decision capture index")
        prefix = observations[: index + 1]
        snapshot = build_support_lifecycle(prefix, min_tick=config.min_tick)
        behavior = build_support_behavior(
            prefix,
            snapshot,
            config=behavior_config,
            min_tick=config.min_tick,
        )
        points[index] = SimpleNamespace(
            symbol=inputs.symbol,
            timeframe="1d",
            input_batch=_prefix_batch(batch, index),
            snapshot=snapshot,
            behavior=behavior,
        )
    return points


def _reference_atr_histories(inputs: AnalysisInputSnapshot) -> dict[str, tuple[float, ...]]:
    return {
        timeframe: _wilder_atr_history(inputs.for_timeframe(timeframe).input_batch.frame)
        for timeframe in inputs.timeframes
    }


class HistoricalDecisionInputReplayRunner:
    """Build causal decision snapshots from one forward replay of each native domain.

    The expensive market engines are never rebuilt once per decision bar. Each engine
    consumes its full closed-bar history once. Point-in-time read models are captured
    only at native bar indices required by the selected 1h decision cutoffs. The
    comparatively cheap context/targeting/decision-input composition is then repeated
    for those frozen causal captures.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store
        self.clock = CausalBarClock()

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> HistoricalDecisionInputReplay:
        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)
        inputs = load_analysis_inputs(
            self.store,
            symbol=clean_symbol,
            timeframes=ANALYSIS_TIMEFRAMES,
        )
        decision_tf = cfg.decision_timeframe.strip().lower()
        decision_frame = inputs.for_timeframe(decision_tf).input_batch.frame.copy()
        decision_frame["timestamp"] = pd.to_datetime(decision_frame["timestamp"], errors="raise")
        if cfg.start_at is not None:
            decision_frame = decision_frame.loc[decision_frame["timestamp"] >= pd.Timestamp(cfg.start_at)]
        if cfg.end_at is not None:
            decision_frame = decision_frame.loc[decision_frame["timestamp"] <= pd.Timestamp(cfg.end_at)]
        if cfg.max_bars is not None:
            decision_frame = decision_frame.tail(cfg.max_bars)
        if decision_frame.empty:
            return HistoricalDecisionInputReplay(clean_symbol, decision_tf, (), ())

        cutoffs = tuple(
            pd.Timestamp(self.clock.available_at(value, decision_tf))
            for value in decision_frame["timestamp"]
        )
        capture_indices = _capture_indices(inputs, cutoffs=cutoffs, clock=self.clock)

        structure_captures, pattern_captures, full_structure = _build_structure_pattern_captures(
            inputs,
            symbol=clean_symbol,
            capture_indices=capture_indices,
            clock=self.clock,
            pattern_profile=cfg.pattern_profile,
        )
        ham_full = HamMTFEvidenceReplayRunner(self.store).replay(
            clean_symbol,
            timeframes=inputs.timeframes,
            input_snapshot=inputs,
        )
        volume_full = VolumeMTFEvidenceReplayRunner(self.store).replay(
            clean_symbol,
            timeframes=inputs.timeframes,
            structure_replay=full_structure,
            input_snapshot=inputs,
        )
        volatility_timeframes = tuple(tf for tf in VOLATILITY_TIMEFRAMES if tf in inputs.timeframes)
        volatility_full = VolatilityMTFReplayRunner(self.store).replay(
            clean_symbol,
            input_snapshot=inputs,
            timeframes=volatility_timeframes,
        )
        liquidity_captures = _build_liquidity_captures(
            inputs,
            symbol=clean_symbol,
            capture_indices=capture_indices,
            clock=self.clock,
        )
        order_block_captures = _build_order_block_captures(
            inputs,
            symbol=clean_symbol,
            capture_indices=capture_indices,
            clock=self.clock,
        )
        fvg_captures = _build_fvg_captures(
            inputs,
            symbol=clean_symbol,
            capture_indices=capture_indices,
            clock=self.clock,
        )
        stabil_by_index = _stabil_points(
            inputs,
            indices_1d=capture_indices["1d"],
        )
        atr_histories = _reference_atr_histories(inputs)
        quality_by_timeframe = {
            timeframe: inputs.for_timeframe(timeframe).input_batch.source_quality.status
            for timeframe in inputs.timeframes
        }

        snapshots: list[DecisionInputSnapshot] = []
        for position, cutoff in enumerate(cutoffs):
            indices = {tf: capture_indices[tf][position] for tf in inputs.timeframes}
            structure = _structure_view(
                inputs,
                symbol=clean_symbol,
                indices=indices,
                captures=structure_captures,
            )
            pattern = _pattern_view(
                symbol=clean_symbol,
                structure=structure,
                indices=indices,
                captures=pattern_captures,
            )
            liquidity = _target_view(
                symbol=clean_symbol,
                indices=indices,
                captures=liquidity_captures,
                kind="liquidity",
            )
            order_block = _target_view(
                symbol=clean_symbol,
                indices=indices,
                captures=order_block_captures,
                kind="order_block",
            )
            fvg = _target_view(
                symbol=clean_symbol,
                indices=indices,
                captures=fvg_captures,
                kind="fvg",
            )
            participation = _volume_view(volume_full, indices, cutoff)
            ham = _ham_view(ham_full, indices)
            volatility_indices = {tf: indices[tf] for tf in volatility_full.timeframes}
            volatility = _volatility_view(volatility_full, volatility_indices)
            stabil = stabil_by_index[indices["1d"]]

            reference_price = float(
                inputs.for_timeframe(decision_tf).input_batch.frame.iloc[indices[decision_tf]]["close"]
            )
            reference_atr_by_timeframe = {
                tf: atr_histories[tf][indices[tf]] for tf in inputs.timeframes
            }
            reference_atr = reference_atr_by_timeframe[decision_tf]

            evidence: list[Any] = []
            structure_by_timeframe = {
                tf: structure.replay_for(tf).market_structure for tf in inputs.timeframes
            }
            if liquidity is not None:
                liq_atr = {
                    tf: liquidity.for_timeframe(tf).atr for tf in liquidity.timeframes
                }
                evidence.extend(
                    enrich_liquidity_scope(
                        liquidity.evidence,
                        structure_by_timeframe=structure_by_timeframe,
                        atr_by_timeframe=liq_atr,
                    )
                )
            for timeframe in inputs.timeframes:
                evidence.extend(
                    support_resistance_evidence(
                        symbol=clean_symbol,
                        timeframe=timeframe,
                        snapshot=structure.replay_for(timeframe).support_resistance,
                        clock=self.clock,
                    )
                )
            if order_block is not None:
                evidence.extend(order_block.evidence)
            if fvg is not None:
                evidence.extend(fvg.evidence)
            deduped = deduplicate_origin_events(evidence, reference_atr=reference_atr)
            targeting = build_targeting_snapshot(
                symbol=clean_symbol,
                as_of=cutoff,
                current_price=reference_price,
                reference_timeframe=decision_tf,
                reference_atr=reference_atr,
                evidence=deduped,
            )
            semantic_targeting = build_semantic_targeting_snapshot(
                symbol=clean_symbol,
                as_of=cutoff,
                current_price=reference_price,
                reference_atr=reference_atr,
                evidence=deduped,
            )

            cross_domain = build_cross_domain_context(
                CrossDomainBuildInputs(
                    symbol=clean_symbol,
                    as_of=cutoff,
                    anchor_timeframe="4h" if "4h" in inputs.timeframes else "2h",
                    current_price=reference_price,
                    structure_location=structure,
                    available_at=self.clock.available_at,
                    data_quality_by_timeframe=quality_by_timeframe,
                    reference_atr_by_timeframe=reference_atr_by_timeframe,
                    pattern_replay=pattern,
                    liquidity_replay=liquidity,
                    order_block_replay=order_block,
                    fvg_engulfing_replay=fvg,
                    stabil_support_replay=stabil,
                    participation_replay=participation,
                    volatility_replay=volatility,
                    ham_replay=ham,
                    requested_timeframes=tuple(inputs.timeframes),
                    trigger_timeframes=tuple(tf for tf in ("1h", "30m") if tf in inputs.timeframes),
                )
            )
            workspace_view = SimpleNamespace(
                cross_domain_result=cross_domain,
                timeframes=tuple(inputs.timeframes),
                targeting_result=targeting,
                semantic_targeting_result=semantic_targeting,
            )
            snapshots.append(build_decision_input_snapshot(workspace_view))

        return HistoricalDecisionInputReplay(
            symbol=clean_symbol,
            decision_timeframe=decision_tf,
            cutoffs=cutoffs,
            snapshots=tuple(snapshots),
        )


def replay_historical_decision_inputs(
    cache_root: str | Path,
    *,
    symbol: str,
    config: HistoricalDecisionInputConfig | None = None,
) -> HistoricalDecisionInputReplay:
    return HistoricalDecisionInputReplayRunner(ParquetOHLCVStore(cache_root)).replay(
        symbol,
        config=config,
    )


__all__ = [
    "HistoricalDecisionInputConfig",
    "HistoricalDecisionInputReplay",
    "HistoricalDecisionInputReplayRunner",
    "replay_historical_decision_inputs",
]
