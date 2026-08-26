from __future__ import annotations

from time import perf_counter
from types import SimpleNamespace
from typing import Any

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.context.builder import CrossDomainBuildInputs, build_cross_domain_context
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision_input import DecisionInputSnapshot, build_decision_input_snapshot
from financial_dashboard.ham_mtf_replay import HamMTFEvidenceReplayRunner
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.targeting.adapters import support_resistance_evidence
from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.clustering import build_targeting_snapshot
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
    _volume_view,
    _volatility_view,
)
from .incremental_targeting import deduplicate_origin_events_indexed


class IncrementalHistoricalDecisionInputReplayRunner:
    """Decision-input producer backed by the shared native-domain timeline.

    Native Structure/S-R/Pattern/Liquidity/OB/FVG engines are advanced by the same
    incremental runtime intended for live catch-up. HAM, Volume and Volatility still
    perform their canonical one-time full passes in this migration step. Derived
    targeting/cross-domain composition remains semantically identical to the old
    path, while origin dedup uses a bounded index proven against the canonical
    grouping rules.
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

        # Process-local Parquet/input caching makes this a cheap identity-preserving
        # read. Keeping it outside NativeDomainState avoids storing full DataFrames in
        # every timeline point.
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

        started = perf_counter()
        snapshots: list[DecisionInputSnapshot] = []
        for domain_point in native.state_store.domains:
            cutoff = domain_point.as_of
            domain = domain_point.state
            indices = {tf: int(domain_point.watermarks[tf]) for tf in inputs.timeframes}
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
                tf: domain.structure.replay_for(tf).market_structure for tf in inputs.timeframes
            }
            if domain.liquidity is not None:
                liq_atr = {
                    tf: domain.liquidity.for_timeframe(tf).atr
                    for tf in domain.liquidity.timeframes
                }
                evidence.extend(
                    enrich_liquidity_scope(
                        domain.liquidity.evidence,
                        structure_by_timeframe=structure_by_timeframe,
                        atr_by_timeframe=liq_atr,
                    )
                )
            for timeframe in inputs.timeframes:
                evidence.extend(
                    support_resistance_evidence(
                        symbol=clean_symbol,
                        timeframe=timeframe,
                        snapshot=domain.structure.replay_for(timeframe).support_resistance,
                        clock=self.clock,
                    )
                )
            if domain.order_block is not None:
                evidence.extend(domain.order_block.evidence)
            if domain.fvg is not None:
                evidence.extend(domain.fvg.evidence)

            deduped = deduplicate_origin_events_indexed(
                evidence,
                reference_atr=reference_atr,
            )
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
