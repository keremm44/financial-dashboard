from __future__ import annotations

from time import perf_counter
from types import SimpleNamespace
from typing import Any

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.context.builder import CrossDomainBuildInputs
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision_input import DecisionInputSnapshot, build_decision_input_snapshot
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.targeting.adapters import support_resistance_evidence
from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.enrichment import enrich_liquidity_scope
from financial_dashboard.volume_mtf_replay import VolumeMTFEvidenceReplayRunner
from financial_dashboard.volatility_mtf_replay import VOLATILITY_TIMEFRAMES, VolatilityMTFReplayRunner

from .history_native_timeline import HistoricalNativeTimelineReplayRunner
from .history_single_pass import HistoricalReplayTimings, SinglePassHistoricalDecisionInputReplay
from .history_source import (
    HistoricalDecisionInputConfig,
    _ham_view,
    _reference_atr_histories,
    _stabil_points,
    _volatility_view,
)
from .incremental_cross_domain import IncrementalCrossDomainProjector
from .incremental_targeting import (
    build_targeting_from_deduped_evidence,
    deduplicate_origin_events_indexed,
)
from .indexed_views import IndexedVolumeView


class IncrementalHistoricalDecisionInputReplayRunner:
    """Canonical decision-input producer backed by the shared causal timeline.

    Native Structure/S-R/Pattern/Liquidity/OB/FVG engines advance once per closed
    bar. Derived evidence and cross-domain projection rows are also reused by exact
    causal timeframe watermark, so a new 1h decision point does not rebuild unchanged
    1d/4h/2h read models. Final as-of filtering/context composition remains causal and
    deterministic on every decision point.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store
        self.clock = CausalBarClock()
        self.last_assembly_breakdown: dict[str, float] = {}

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> SinglePassHistoricalDecisionInputReplay:
        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)
        self.last_assembly_breakdown = {}

        native = HistoricalNativeTimelineReplayRunner(self.store).replay(
            clean_symbol,
            config=cfg,
        )
        if not native.cutoffs:
            timings = HistoricalReplayTimings(
                load_inputs_seconds=native.timings.load_inputs_seconds,
                native_capture_pass_seconds=native.timings.native_reduce_seconds,
                ham_seconds=0.0,
                volume_seconds=0.0,
                volatility_seconds=0.0,
                stabil_seconds=0.0,
                snapshot_assembly_seconds=0.0,
            )
            return SinglePassHistoricalDecisionInputReplay(
                symbol=clean_symbol,
                decision_timeframe=cfg.decision_timeframe.strip().lower(),
                cutoffs=(),
                snapshots=(),
                timings=timings,
            )
        if native.full_state is None:
            raise RuntimeError("incremental native replay has cutoffs but no full state")

        started = perf_counter()
        inputs = load_analysis_inputs(
            self.store,
            symbol=clean_symbol,
            timeframes=ANALYSIS_TIMEFRAMES,
        )
        secondary_load_seconds = perf_counter() - started

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
            structure_replay=native.full_state.structure,
            input_snapshot=inputs,
        )
        volume_seconds = perf_counter() - started
        volume_view = IndexedVolumeView(volume_full)

        started = perf_counter()
        volatility_timeframes = tuple(tf for tf in VOLATILITY_TIMEFRAMES if tf in inputs.timeframes)
        volatility_full = VolatilityMTFReplayRunner(self.store).replay(
            clean_symbol,
            input_snapshot=inputs,
            timeframes=volatility_timeframes,
        )
        volatility_seconds = perf_counter() - started

        watermarks = tuple(point.watermarks for point in native.state_store.domains)
        indices_1d = tuple(int(item["1d"]) for item in watermarks)
        started = perf_counter()
        stabil_by_index = _stabil_points(inputs, indices_1d=indices_1d)
        stabil_seconds = perf_counter() - started

        atr_histories = _reference_atr_histories(inputs)
        quality_by_timeframe = {
            timeframe: inputs.for_timeframe(timeframe).input_batch.source_quality.status
            for timeframe in inputs.timeframes
        }
        decision_tf = cfg.decision_timeframe.strip().lower()

        snapshots: list[DecisionInputSnapshot] = []
        support_evidence_cache: dict[tuple[str, int], tuple[Any, ...]] = {}
        liquidity_evidence_cache: dict[tuple[str, int], tuple[Any, ...]] = {}
        projector = IncrementalCrossDomainProjector()
        breakdown = {
            "views": 0.0,
            "evidence": 0.0,
            "dedup": 0.0,
            "targeting": 0.0,
            "semantic_targeting": 0.0,
            "cross_domain": 0.0,
            "decision_input": 0.0,
        }

        assembly_started = perf_counter()
        for domain_point in native.state_store.domains:
            cutoff = domain_point.as_of
            domain = domain_point.state
            indices = {tf: int(domain_point.watermarks[tf]) for tf in inputs.timeframes}

            stage = perf_counter()
            participation = volume_view.at(indices, cutoff)
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
            breakdown["views"] += perf_counter() - stage

            stage = perf_counter()
            evidence: list[Any] = []
            for timeframe in inputs.timeframes:
                index = indices[timeframe]
                structure_row = domain.structure.replay_for(timeframe)

                if domain.liquidity is not None and timeframe in domain.liquidity.timeframes:
                    cache_key = (timeframe, index)
                    liquidity_rows = liquidity_evidence_cache.get(cache_key)
                    if liquidity_rows is None:
                        snapshot = domain.liquidity.snapshots[timeframe]
                        liquidity_rows = enrich_liquidity_scope(
                            snapshot.evidence,
                            structure_by_timeframe={
                                timeframe: structure_row.market_structure,
                            },
                            atr_by_timeframe={timeframe: snapshot.atr},
                        )
                        liquidity_evidence_cache[cache_key] = liquidity_rows
                    evidence.extend(liquidity_rows)

                cache_key = (timeframe, index)
                support_rows = support_evidence_cache.get(cache_key)
                if support_rows is None:
                    support_rows = support_resistance_evidence(
                        symbol=clean_symbol,
                        timeframe=timeframe,
                        snapshot=structure_row.support_resistance,
                        clock=self.clock,
                    )
                    support_evidence_cache[cache_key] = support_rows
                evidence.extend(support_rows)

                if domain.order_block is not None and timeframe in domain.order_block.timeframes:
                    evidence.extend(domain.order_block.snapshots[timeframe].evidence)
                if domain.fvg is not None and timeframe in domain.fvg.timeframes:
                    evidence.extend(domain.fvg.snapshots[timeframe].evidence)
            breakdown["evidence"] += perf_counter() - stage

            stage = perf_counter()
            deduped = deduplicate_origin_events_indexed(
                evidence,
                reference_atr=reference_atr,
            )
            breakdown["dedup"] += perf_counter() - stage

            stage = perf_counter()
            targeting = build_targeting_from_deduped_evidence(
                symbol=clean_symbol,
                as_of=cutoff,
                current_price=reference_price,
                reference_timeframe=decision_tf,
                reference_atr=reference_atr,
                evidence=deduped,
            )
            breakdown["targeting"] += perf_counter() - stage

            stage = perf_counter()
            semantic_targeting = build_semantic_targeting_snapshot(
                symbol=clean_symbol,
                as_of=cutoff,
                current_price=reference_price,
                reference_atr=reference_atr,
                evidence=deduped,
            )
            breakdown["semantic_targeting"] += perf_counter() - stage

            build_inputs = CrossDomainBuildInputs(
                symbol=clean_symbol,
                as_of=cutoff,
                anchor_timeframe="4h" if "4h" in inputs.timeframes else "2h",
                current_price=reference_price,
                structure_location=domain.structure,
                available_at=self.clock.available_at,
                data_quality_by_timeframe=quality_by_timeframe,
                reference_atr_by_timeframe=reference_atr_by_timeframe,
                pattern_replay=domain.pattern,
                liquidity_replay=domain.liquidity,
                order_block_replay=domain.order_block,
                fvg_engulfing_replay=domain.fvg,
                stabil_support_replay=stabil,
                participation_replay=participation,
                volatility_replay=volatility,
                ham_replay=ham,
                requested_timeframes=tuple(inputs.timeframes),
                trigger_timeframes=tuple(tf for tf in ("1h", "30m") if tf in inputs.timeframes),
            )
            stage = perf_counter()
            cross_domain = projector.build(build_inputs, watermarks=indices)
            breakdown["cross_domain"] += perf_counter() - stage

            stage = perf_counter()
            workspace_view = SimpleNamespace(
                cross_domain_result=cross_domain,
                timeframes=tuple(inputs.timeframes),
                targeting_result=targeting,
                semantic_targeting_result=semantic_targeting,
            )
            snapshots.append(build_decision_input_snapshot(workspace_view))
            breakdown["decision_input"] += perf_counter() - stage

        assembly_seconds = perf_counter() - assembly_started
        self.last_assembly_breakdown = breakdown

        timings = HistoricalReplayTimings(
            load_inputs_seconds=native.timings.load_inputs_seconds + secondary_load_seconds,
            native_capture_pass_seconds=(
                native.timings.event_build_seconds + native.timings.native_reduce_seconds
            ),
            ham_seconds=ham_seconds,
            volume_seconds=volume_seconds,
            volatility_seconds=volatility_seconds,
            stabil_seconds=stabil_seconds,
            snapshot_assembly_seconds=assembly_seconds,
        )
        return SinglePassHistoricalDecisionInputReplay(
            symbol=clean_symbol,
            decision_timeframe=decision_tf,
            cutoffs=native.cutoffs,
            snapshots=tuple(snapshots),
            timings=timings,
        )


__all__ = ["IncrementalHistoricalDecisionInputReplayRunner"]
