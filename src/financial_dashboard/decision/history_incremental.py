from __future__ import annotations

from time import perf_counter
from types import SimpleNamespace
from typing import Any

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.context.builder import CrossDomainBuildInputs
from financial_dashboard.data.analysis_inputs import cache_fingerprint, load_analysis_inputs
from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision_input import DecisionInputSnapshot, build_decision_input_snapshot
from financial_dashboard.structure_location_replay import CausalBarClock
from financial_dashboard.targeting.adapters import support_resistance_evidence
from financial_dashboard.targeting.arrival import build_semantic_targeting_snapshot
from financial_dashboard.targeting.enrichment import enrich_liquidity_scope

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
from .persistent_state import (
    PersistentCacheIdentity,
    PersistentCheckpointIdentity,
    PersistentCheckpointRecord,
    PersistentObjectStore,
    build_prefix_fingerprints,
    validate_append_only_prefix,
)
from .supporting_replay_runtime import (
    IncrementalSupportingReplayRuntime,
    SupportingRuntimeCheckpoint,
)


_DECISION_PERSISTENCE_SEMANTIC_VERSION = "decision-input-persistent-v2"
_DECISION_APPEND_SEMANTIC_VERSION = "decision-input-append-checkpoint-v1"
_SUPPORTING_PERSISTENCE_SEMANTIC_VERSION = "supporting-runtime-checkpoint-v1"


def _config_fingerprint(config: HistoricalDecisionInputConfig) -> str:
    return repr(
        (
            config.decision_timeframe.strip().lower(),
            config.pattern_profile,
            config.max_bars,
            None if config.start_at is None else str(pd.Timestamp(config.start_at)),
            None if config.end_at is None else str(pd.Timestamp(config.end_at)),
        )
    )


def _zero_timings() -> HistoricalReplayTimings:
    return HistoricalReplayTimings(
        load_inputs_seconds=0.0,
        native_capture_pass_seconds=0.0,
        ham_seconds=0.0,
        volume_seconds=0.0,
        volatility_seconds=0.0,
        stabil_seconds=0.0,
        snapshot_assembly_seconds=0.0,
    )


def _cutoffs_are_prefix(previous: tuple[Any, ...], current: tuple[Any, ...]) -> bool:
    if len(previous) > len(current):
        return False
    return all(
        pd.Timestamp(left) == pd.Timestamp(right)
        for left, right in zip(previous, current[: len(previous)], strict=True)
    )


def _last_watermarks(inputs) -> dict[str, int]:
    return {
        timeframe: len(inputs.for_timeframe(timeframe).input_batch.frame) - 1
        for timeframe in inputs.timeframes
    }


class IncrementalHistoricalDecisionInputReplayRunner:
    """Persistent canonical decision-input producer.

    Cold run:
      raw OHLCV -> native/supporting engines -> frozen decision timeline -> disk.

    Exact warm run:
      persisted DecisionInputSnapshot timeline -> BUY/SELL; no domain replay.

    Append run:
      validate the old OHLCV prefix, restore native + supporting engine checkpoints,
      ingest only unseen closed rows, compose only newly added decision cutoffs, append
      them to the frozen timeline and atomically checkpoint the new state.

    Any config/schema/history mismatch fails closed to the canonical cold path. Market
    semantics, causal availability and BUY/SELL rules are unchanged by persistence.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store
        self.clock = CausalBarClock()
        self.last_assembly_breakdown: dict[str, float] = {}
        self.last_persistent_cache_status = "UNSET"
        self.last_native_checkpoint_status = "UNSET"
        self.last_supporting_checkpoint_status = "UNSET"
        self.last_decision_append_status = "UNSET"

    def _cache_identity(
        self,
        *,
        symbol: str,
        config: HistoricalDecisionInputConfig,
    ) -> PersistentCacheIdentity:
        return PersistentCacheIdentity(
            namespace="decision_input_timeline",
            symbol=symbol,
            semantic_fingerprint=_DECISION_PERSISTENCE_SEMANTIC_VERSION,
            config_fingerprint=_config_fingerprint(config),
            source_fingerprint=cache_fingerprint(
                self.store,
                symbol=symbol,
                timeframes=ANALYSIS_TIMEFRAMES,
            ),
        )

    def _decision_checkpoint_identity(
        self,
        *,
        symbol: str,
        config: HistoricalDecisionInputConfig,
    ) -> PersistentCheckpointIdentity:
        return PersistentCheckpointIdentity(
            namespace="decision_input_timeline",
            symbol=symbol,
            semantic_fingerprint=_DECISION_APPEND_SEMANTIC_VERSION,
            config_fingerprint=_config_fingerprint(config),
        )

    def _supporting_checkpoint_identity(
        self,
        *,
        symbol: str,
    ) -> PersistentCheckpointIdentity:
        return PersistentCheckpointIdentity(
            namespace="supporting_runtime",
            symbol=symbol,
            semantic_fingerprint=_SUPPORTING_PERSISTENCE_SEMANTIC_VERSION,
            config_fingerprint="HAM-default|Volume-default|Volatility-Dengeli",
        )

    @staticmethod
    def _cached_prefix_result(
        record: PersistentCheckpointRecord | None,
        *,
        inputs,
    ) -> SinglePassHistoricalDecisionInputReplay | None:
        if record is None or not validate_append_only_prefix(inputs, record.prefixes):
            return None
        return (
            record.payload
            if isinstance(record.payload, SinglePassHistoricalDecisionInputReplay)
            else None
        )

    def _save_decision_checkpoints(
        self,
        *,
        persistent: PersistentObjectStore,
        exact_identity: PersistentCacheIdentity,
        append_identity: PersistentCheckpointIdentity,
        inputs,
        result: SinglePassHistoricalDecisionInputReplay,
    ) -> None:
        exact_saved = False
        append_saved = False
        try:
            persistent.save(exact_identity, result)
            exact_saved = True
        except Exception:
            pass
        try:
            persistent.save_checkpoint(
                PersistentCheckpointRecord(
                    identity=append_identity,
                    prefixes=build_prefix_fingerprints(
                        inputs,
                        watermarks=_last_watermarks(inputs),
                    ),
                    cursor=None,
                    payload=result,
                )
            )
            append_saved = True
        except Exception:
            pass
        self.last_persistent_cache_status = "SAVED" if exact_saved else "SAVE_FAILED"
        self.last_decision_append_status = "SAVED" if append_saved else "SAVE_FAILED"

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> SinglePassHistoricalDecisionInputReplay:
        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)
        self.last_assembly_breakdown = {}
        self.last_persistent_cache_status = "MISS"
        self.last_native_checkpoint_status = "UNSET"
        self.last_supporting_checkpoint_status = "UNSET"
        self.last_decision_append_status = "MISS"

        persistent = PersistentObjectStore(self.store.root)
        exact_identity = self._cache_identity(symbol=clean_symbol, config=cfg)
        cached = persistent.load(exact_identity)
        if isinstance(cached, SinglePassHistoricalDecisionInputReplay):
            self.last_persistent_cache_status = "HIT_EXACT"
            self.last_decision_append_status = "NOT_NEEDED"
            self.last_assembly_breakdown = {
                "views": 0.0,
                "evidence": 0.0,
                "dedup": 0.0,
                "targeting": 0.0,
                "semantic_targeting": 0.0,
                "cross_domain": 0.0,
                "decision_input": 0.0,
            }
            return SinglePassHistoricalDecisionInputReplay(
                symbol=cached.symbol,
                decision_timeframe=cached.decision_timeframe,
                cutoffs=cached.cutoffs,
                snapshots=cached.snapshots,
                timings=_zero_timings(),
            )

        started = perf_counter()
        inputs = load_analysis_inputs(
            self.store,
            symbol=clean_symbol,
            timeframes=ANALYSIS_TIMEFRAMES,
        )
        secondary_load_seconds = perf_counter() - started

        append_identity = self._decision_checkpoint_identity(symbol=clean_symbol, config=cfg)
        previous_result = self._cached_prefix_result(
            persistent.load_checkpoint(append_identity),
            inputs=inputs,
        )

        native_runner = HistoricalNativeTimelineReplayRunner(self.store)
        native = native_runner.replay(clean_symbol, config=cfg)
        self.last_native_checkpoint_status = native_runner.last_checkpoint_status
        if not native.cutoffs:
            result = SinglePassHistoricalDecisionInputReplay(
                symbol=clean_symbol,
                decision_timeframe=cfg.decision_timeframe.strip().lower(),
                cutoffs=(),
                snapshots=(),
                timings=HistoricalReplayTimings(
                    load_inputs_seconds=native.timings.load_inputs_seconds + secondary_load_seconds,
                    native_capture_pass_seconds=native.timings.native_reduce_seconds,
                    ham_seconds=0.0,
                    volume_seconds=0.0,
                    volatility_seconds=0.0,
                    stabil_seconds=0.0,
                    snapshot_assembly_seconds=0.0,
                ),
            )
            self._save_decision_checkpoints(
                persistent=persistent,
                exact_identity=exact_identity,
                append_identity=append_identity,
                inputs=inputs,
                result=result,
            )
            return result
        if native.full_state is None:
            raise RuntimeError("incremental native replay has cutoffs but no full state")

        append_base: SinglePassHistoricalDecisionInputReplay | None = None
        if previous_result is not None and _cutoffs_are_prefix(previous_result.cutoffs, native.cutoffs):
            append_base = previous_result
            if len(previous_result.cutoffs) == len(native.cutoffs):
                # The source may have gained bars after the final requested decision
                # cutoff. Those bars cannot change already-frozen historical decisions.
                result = SinglePassHistoricalDecisionInputReplay(
                    symbol=previous_result.symbol,
                    decision_timeframe=previous_result.decision_timeframe,
                    cutoffs=previous_result.cutoffs,
                    snapshots=previous_result.snapshots,
                    timings=HistoricalReplayTimings(
                        load_inputs_seconds=native.timings.load_inputs_seconds + secondary_load_seconds,
                        native_capture_pass_seconds=(
                            native.timings.event_build_seconds + native.timings.native_reduce_seconds
                        ),
                        ham_seconds=0.0,
                        volume_seconds=0.0,
                        volatility_seconds=0.0,
                        stabil_seconds=0.0,
                        snapshot_assembly_seconds=0.0,
                    ),
                )
                self.last_decision_append_status = "HIT_NO_NEW_CUTOFF"
                self._save_decision_checkpoints(
                    persistent=persistent,
                    exact_identity=exact_identity,
                    append_identity=append_identity,
                    inputs=inputs,
                    result=result,
                )
                return result
            self.last_decision_append_status = "HIT_APPEND"

        supporting_identity = self._supporting_checkpoint_identity(symbol=clean_symbol)
        supporting_record = persistent.load_checkpoint(supporting_identity)
        supporting_runtime = IncrementalSupportingReplayRuntime(
            inputs,
            symbol=clean_symbol,
            clock=self.clock,
        )
        if (
            supporting_record is not None
            and isinstance(supporting_record.payload, SupportingRuntimeCheckpoint)
            and validate_append_only_prefix(inputs, supporting_record.prefixes)
        ):
            try:
                supporting_runtime.restore_checkpoint(supporting_record.payload)
            except Exception:
                self.last_supporting_checkpoint_status = "RESTORE_FAILED"
            else:
                self.last_supporting_checkpoint_status = "HIT"
        else:
            self.last_supporting_checkpoint_status = "MISS"

        supporting_started = perf_counter()
        supporting_runtime.advance()
        supporting = supporting_runtime.freeze(structure_replay=native.full_state.structure)
        supporting_seconds = perf_counter() - supporting_started
        try:
            persistent.save_checkpoint(
                PersistentCheckpointRecord(
                    identity=supporting_identity,
                    prefixes=build_prefix_fingerprints(
                        inputs,
                        watermarks=supporting_runtime.watermarks,
                    ),
                    cursor=dict(supporting_runtime.watermarks),
                    payload=supporting_runtime.export_checkpoint(),
                )
            )
            self.last_supporting_checkpoint_status += "_SAVED"
        except Exception:
            self.last_supporting_checkpoint_status += "_SAVE_FAILED"

        ham_full = supporting.ham
        volume_full = supporting.volume
        volatility_full = supporting.volatility
        volume_view = IndexedVolumeView(volume_full)

        start_position = 0 if append_base is None else len(append_base.snapshots)
        if native.state_store_start_position == 0:
            # Cold/native fallback still exposes the full historical state store.
            points_to_assemble = native.state_store.domains[start_position:]
        elif append_base is not None and native.state_store_start_position == start_position:
            # Checkpoint resume intentionally exposes only newly appended native points;
            # the frozen decision timeline already owns the historical prefix.
            points_to_assemble = native.state_store.domains
        else:
            raise RuntimeError(
                "native checkpoint delta is not aligned with the persisted decision prefix: "
                f"native starts at {native.state_store_start_position}, decision prefix is {start_position}"
            )
        selected_watermarks = tuple(point.watermarks for point in points_to_assemble)
        selected_indices_1d = tuple(int(item["1d"]) for item in selected_watermarks)

        started = perf_counter()
        stabil_by_index = (
            _stabil_points(inputs, indices_1d=selected_indices_1d)
            if selected_indices_1d
            else {}
        )
        stabil_seconds = perf_counter() - started

        atr_histories = _reference_atr_histories(inputs)
        quality_by_timeframe = {
            timeframe: inputs.for_timeframe(timeframe).input_batch.source_quality.status
            for timeframe in inputs.timeframes
        }
        decision_tf = cfg.decision_timeframe.strip().lower()

        snapshots: list[DecisionInputSnapshot] = (
            [] if append_base is None else list(append_base.snapshots)
        )
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
        for domain_point in points_to_assemble:
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
            # Supporting engines are checkpointed and advanced together. Keep their
            # combined current-run cost in Volume until the timing contract grows a
            # dedicated supporting-runtime field; the aggregate native total remains exact.
            ham_seconds=0.0,
            volume_seconds=supporting_seconds,
            volatility_seconds=0.0,
            stabil_seconds=stabil_seconds,
            snapshot_assembly_seconds=assembly_seconds,
        )
        result = SinglePassHistoricalDecisionInputReplay(
            symbol=clean_symbol,
            decision_timeframe=decision_tf,
            cutoffs=native.cutoffs,
            snapshots=tuple(snapshots),
            timings=timings,
        )
        self._save_decision_checkpoints(
            persistent=persistent,
            exact_identity=exact_identity,
            append_identity=append_identity,
            inputs=inputs,
            result=result,
        )
        if append_base is not None and self.last_decision_append_status == "SAVED":
            self.last_decision_append_status = "HIT_APPEND_SAVED"
        return result


__all__ = ["IncrementalHistoricalDecisionInputReplayRunner"]
