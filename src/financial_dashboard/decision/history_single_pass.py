from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.context.builder import CrossDomainBuildInputs, build_cross_domain_context
from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_source import (
    HistoricalDecisionInputConfig,
    HistoricalDecisionInputReplay,
    _TargetCapture,
    _StructureCapture,
    _capture_indices,
    _ham_view,
    _pattern_snapshot,
    _pattern_view,
    _prefix_batch,
    _reference_atr_histories,
    _stabil_points,
    _structure_view,
    _target_view,
    _volume_view,
    _volatility_view,
    _wilder_atr_history,
)
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
from financial_dashboard.engines.support_resistance_runtime_engine import RuntimeSupportResistanceRangeEngine
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.mtf_replay import market_structure_timeframe_snapshot
from financial_dashboard.structure_location_replay import (
    CausalBarClock,
    StructureLocationMTFResult,
    StructureLocationTimeframeReplay,
    _support_snapshot,
)
from financial_dashboard.target_evidence_replay import FvgEngulfingMTFReplayRunner
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
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner
from financial_dashboard.volatility_mtf_replay import VOLATILITY_TIMEFRAMES, VolatilityMTFReplayRunner


@dataclass(frozen=True, slots=True)
class HistoricalReplayTimings:
    load_inputs_seconds: float
    native_capture_pass_seconds: float
    ham_seconds: float
    volume_seconds: float
    volatility_seconds: float
    stabil_seconds: float
    snapshot_assembly_seconds: float

    @property
    def native_replay_seconds(self) -> float:
        return (
            self.native_capture_pass_seconds
            + self.ham_seconds
            + self.volume_seconds
            + self.volatility_seconds
            + self.stabil_seconds
        )

    @property
    def total_seconds(self) -> float:
        return self.load_inputs_seconds + self.native_replay_seconds + self.snapshot_assembly_seconds


@dataclass(frozen=True, slots=True)
class SinglePassHistoricalDecisionInputReplay:
    symbol: str
    decision_timeframe: str
    cutoffs: tuple[Any, ...]
    snapshots: tuple[DecisionInputSnapshot, ...]
    timings: HistoricalReplayTimings


@dataclass(frozen=True, slots=True)
class _NativeCaptureBundle:
    structure: dict[str, dict[int, _StructureCapture]]
    pattern: dict[str, dict[int, Any]]
    liquidity: dict[str, dict[int, _TargetCapture]]
    order_block: dict[str, dict[int, _TargetCapture]]
    fvg: dict[str, dict[int, _TargetCapture]]
    full_structure: StructureLocationMTFResult


def _single_native_capture_pass(
    inputs: AnalysisInputSnapshot,
    *,
    symbol: str,
    capture_indices: Mapping[str, tuple[int, ...]],
    clock: CausalBarClock,
    pattern_profile: str | None,
) -> _NativeCaptureBundle:
    """Advance the capture-only native engines exactly once per closed bar.

    Structure, S/R, Pattern, Liquidity, OB and FVG previously had independent
    historical loops. This pass advances them together over each timeframe so a
    historical decision count never multiplies native replay work.
    """

    structure_captures: dict[str, dict[int, _StructureCapture]] = {}
    pattern_captures: dict[str, dict[int, Any]] = {}
    liquidity_captures: dict[str, dict[int, _TargetCapture]] = {}
    order_block_captures: dict[str, dict[int, _TargetCapture]] = {}
    fvg_captures: dict[str, dict[int, _TargetCapture]] = {}
    final_replays: dict[str, StructureLocationTimeframeReplay] = {}

    pattern_config = None if pattern_profile is None else PatternCompressionConfig(profile=pattern_profile)
    liquidity_config = LiquidityConfig()
    order_block_config = OrderBlockConfig()

    for timeframe in inputs.timeframes:
        batch = inputs.for_timeframe(timeframe).input_batch
        frame = batch.frame
        wanted = set(capture_indices[timeframe])
        if not wanted:
            continue

        atrs = _wilder_atr_history(frame)
        liquidity_atrs = (
            atrs
            if liquidity_config.atr_length == 14
            else _wilder_atr_history(frame, liquidity_config.atr_length)
        )

        market_engine = MarketStructureEngine()
        support_engine = RuntimeSupportResistanceRangeEngine()
        pattern_engine = RuntimePatternCompressionEngine(pattern_config)
        liquidity_engine = LiquidityEngine(liquidity_config)
        order_block_engine = OrderBlockEngine(order_block_config)
        order_block_behavior = OrderBlockBehaviorTracker(order_block_config)
        fvg_engine = (
            FvgEngulfingEngine(FvgEngulfingConfig(timeframe=timeframe))
            if timeframe in SUPPORTED_TIMEFRAMES
            else None
        )

        liquidity_confirmations: dict[str, tuple[object, object]] = {}
        order_block_confirmations: dict[str, tuple[object, object]] = {}
        fvg_confirmations: dict[str, tuple[object, object]] = {}
        latest_ob_behavior: tuple[Any, ...] = ()

        tf_structure: dict[int, _StructureCapture] = {}
        tf_pattern: dict[int, Any] = {}
        tf_liquidity: dict[int, _TargetCapture] = {}
        tf_order_block: dict[int, _TargetCapture] = {}
        tf_fvg: dict[int, _TargetCapture] = {}

        for index, row in enumerate(frame.to_dict("records")):
            market_engine.update(row)
            support_engine.update(row)
            pattern_engine.update(row)

            liquidity_engine.update(row)
            for pool in liquidity_engine.pools:
                if pool.identity not in liquidity_confirmations and pool.state in {
                    LiquidityPoolState.ACTIVE,
                    LiquidityPoolState.TESTED,
                }:
                    confirmed_at = row["timestamp"]
                    liquidity_confirmations[pool.identity] = (
                        confirmed_at,
                        clock.available_at(confirmed_at, timeframe),
                    )

            order_block_engine.update(row)
            latest_ob_behavior = order_block_behavior.update(
                order_block_engine.records,
                bar_index=index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for record in order_block_engine.records:
                identity = f"OB:{timeframe}:{record.source_index}:{1 if record.bullish else -1}"
                if identity not in order_block_confirmations and record.active:
                    confirmed_at = row["timestamp"]
                    order_block_confirmations[identity] = (
                        confirmed_at,
                        clock.available_at(confirmed_at, timeframe),
                    )

            if fvg_engine is not None:
                fvg_engine.update(row)
                active_records = (
                    fvg_engine.active_bullish_fvg,
                    fvg_engine.active_bearish_fvg,
                    fvg_engine.active_bullish_engulfing,
                    fvg_engine.active_bearish_engulfing,
                )
                for record in active_records:
                    if record is None:
                        continue
                    prefix = "FVG" if hasattr(record, "gap_size") else "ENG"
                    identity = f"{prefix}:{timeframe}:{record.formation_index}:{int(record.direction)}"
                    if identity not in fvg_confirmations:
                        confirmed_at = row["timestamp"]
                        fvg_confirmations[identity] = (
                            confirmed_at,
                            clock.available_at(confirmed_at, timeframe),
                        )

            if index not in wanted:
                continue

            prefix_batch = _prefix_batch(batch, index)
            market = market_structure_timeframe_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                batch=prefix_batch,
                engine=market_engine,
            )
            support = _support_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                batch=prefix_batch,
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

            liq_evidence = liquidity_evidence(
                symbol=symbol,
                timeframe=timeframe,
                engine=liquidity_engine,
                confirmations=liquidity_confirmations,
            )
            liq_snapshot = TargetEvidenceSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                as_of=row["timestamp"],
                available_at=clock.available_at(row["timestamp"], timeframe),
                current_price=float(row["close"]),
                atr=liquidity_atrs[index],
                evidence=liq_evidence,
            )
            tf_liquidity[index] = _TargetCapture(
                index=index,
                snapshot=liq_snapshot,
                evidence=liq_evidence,
                liquidity_behavior=liquidity_engine.behavior_snapshot,
            )

            ob_evidence = order_block_evidence(
                symbol=symbol,
                timeframe=timeframe,
                engine=order_block_engine,
                confirmations=order_block_confirmations,
            )
            behavior_state = {item.identity: item.state.value for item in latest_ob_behavior}
            prefix = f"OB:{timeframe}:"
            ob_evidence = tuple(
                replace(
                    item,
                    source_state=behavior_state.get(
                        str(item.native_origin_id).replace(prefix, "OB:", 1),
                        item.source_state,
                    ),
                )
                for item in ob_evidence
            )
            ob_snapshot = TargetEvidenceSnapshot(
                symbol=symbol,
                timeframe=timeframe,
                as_of=row["timestamp"],
                available_at=clock.available_at(row["timestamp"], timeframe),
                current_price=float(row["close"]),
                atr=atrs[index],
                evidence=ob_evidence,
            )
            tf_order_block[index] = _TargetCapture(
                index=index,
                snapshot=ob_snapshot,
                evidence=ob_evidence,
                order_block_behavior=latest_ob_behavior,
            )

            if fvg_engine is not None:
                fg_evidence = fvg_engulfing_evidence(
                    symbol=symbol,
                    timeframe=timeframe,
                    engine=fvg_engine,
                    confirmations=fvg_confirmations,
                )
                fg_snapshot = TargetEvidenceSnapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    as_of=row["timestamp"],
                    available_at=clock.available_at(row["timestamp"], timeframe),
                    current_price=float(row["close"]),
                    atr=atrs[index],
                    evidence=fg_evidence,
                )
                tf_fvg[index] = _TargetCapture(
                    index=index,
                    snapshot=fg_snapshot,
                    evidence=fg_evidence,
                    fvg_lifecycle=FvgEngulfingMTFReplayRunner._fvg_snapshots(timeframe, fvg_engine),
                    engulfing_lifecycle=FvgEngulfingMTFReplayRunner._engulfing_snapshots(timeframe, fvg_engine),
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
        liquidity_captures[timeframe] = tf_liquidity
        order_block_captures[timeframe] = tf_order_block
        if fvg_engine is not None:
            fvg_captures[timeframe] = tf_fvg

    full_structure = StructureLocationMTFResult(
        symbol=symbol,
        timeframes=tuple(inputs.timeframes),
        replays=final_replays,
        confluence=(),
        location_outcomes=(),
        event_zone_links=(),
    )
    return _NativeCaptureBundle(
        structure=structure_captures,
        pattern=pattern_captures,
        liquidity=liquidity_captures,
        order_block=order_block_captures,
        fvg=fvg_captures,
        full_structure=full_structure,
    )


class SinglePassHistoricalDecisionInputReplayRunner:
    """Historical decision input built from one native forward pass per domain.

    Decision points only select frozen states. Increasing the number of 1h decision
    points must not cause a native engine to replay the same market history again.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store
        self.clock = CausalBarClock()

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> SinglePassHistoricalDecisionInputReplay:
        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)

        started = perf_counter()
        inputs = load_analysis_inputs(
            self.store,
            symbol=clean_symbol,
            timeframes=ANALYSIS_TIMEFRAMES,
        )
        load_seconds = perf_counter() - started

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
            empty_timings = HistoricalReplayTimings(load_seconds, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            return SinglePassHistoricalDecisionInputReplay(clean_symbol, decision_tf, (), (), empty_timings)

        cutoffs = tuple(
            pd.Timestamp(self.clock.available_at(value, decision_tf))
            for value in decision_frame["timestamp"]
        )
        capture_indices = _capture_indices(inputs, cutoffs=cutoffs, clock=self.clock)

        started = perf_counter()
        native = _single_native_capture_pass(
            inputs,
            symbol=clean_symbol,
            capture_indices=capture_indices,
            clock=self.clock,
            pattern_profile=cfg.pattern_profile,
        )
        native_capture_seconds = perf_counter() - started

        started = perf_counter()
        ham_full = HamMTFEvidenceReplayRunner(self.store).replay(
            clean_symbol,
            timeframes=inputs.timeframes,
            input_snapshot=inputs,
        )
        ham_seconds = perf_counter() - started

        started = perf_counter()
        volume_full = VolumeMTFEvidenceReplayRunner(self.store).replay(
            clean_symbol,
            timeframes=inputs.timeframes,
            structure_replay=native.full_structure,
            input_snapshot=inputs,
        )
        volume_seconds = perf_counter() - started

        started = perf_counter()
        volatility_timeframes = tuple(tf for tf in VOLATILITY_TIMEFRAMES if tf in inputs.timeframes)
        volatility_full = VolatilityMTFReplayRunner(self.store).replay(
            clean_symbol,
            input_snapshot=inputs,
            timeframes=volatility_timeframes,
        )
        volatility_seconds = perf_counter() - started

        started = perf_counter()
        stabil_by_index = _stabil_points(
            inputs,
            indices_1d=capture_indices["1d"],
        )
        stabil_seconds = perf_counter() - started

        atr_histories = _reference_atr_histories(inputs)
        quality_by_timeframe = {
            timeframe: inputs.for_timeframe(timeframe).input_batch.source_quality.status
            for timeframe in inputs.timeframes
        }

        started = perf_counter()
        snapshots: list[DecisionInputSnapshot] = []
        for position, cutoff in enumerate(cutoffs):
            indices = {tf: capture_indices[tf][position] for tf in inputs.timeframes}
            structure = _structure_view(
                inputs,
                symbol=clean_symbol,
                indices=indices,
                captures=native.structure,
            )
            pattern = _pattern_view(
                symbol=clean_symbol,
                structure=structure,
                indices=indices,
                captures=native.pattern,
            )
            liquidity = _target_view(
                symbol=clean_symbol,
                indices=indices,
                captures=native.liquidity,
                kind="liquidity",
            )
            order_block = _target_view(
                symbol=clean_symbol,
                indices=indices,
                captures=native.order_block,
                kind="order_block",
            )
            fvg = _target_view(
                symbol=clean_symbol,
                indices=indices,
                captures=native.fvg,
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
                liq_atr = {tf: liquidity.for_timeframe(tf).atr for tf in liquidity.timeframes}
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
        assembly_seconds = perf_counter() - started

        timings = HistoricalReplayTimings(
            load_inputs_seconds=load_seconds,
            native_capture_pass_seconds=native_capture_seconds,
            ham_seconds=ham_seconds,
            volume_seconds=volume_seconds,
            volatility_seconds=volatility_seconds,
            stabil_seconds=stabil_seconds,
            snapshot_assembly_seconds=assembly_seconds,
        )
        return SinglePassHistoricalDecisionInputReplay(
            symbol=clean_symbol,
            decision_timeframe=decision_tf,
            cutoffs=cutoffs,
            snapshots=tuple(snapshots),
            timings=timings,
        )


__all__ = [
    "HistoricalReplayTimings",
    "SinglePassHistoricalDecisionInputReplay",
    "SinglePassHistoricalDecisionInputReplayRunner",
]
