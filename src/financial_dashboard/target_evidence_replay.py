from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .analysis_config import ANALYSIS_TIMEFRAMES, normalize_timeframes
from .data.analysis_inputs import AnalysisInputSnapshot, load_analysis_inputs
from .data.identity import normalize_symbol
from .data.parquet_store import ParquetOHLCVStore
from .engines.fvg_engulfing import FvgEngulfingEngine
from .engines.fvg_engulfing_models import FvgEngulfingConfig, SUPPORTED_TIMEFRAMES
from .engines.liquidity_behavior import LiquidityBehaviorSnapshot
from .engines.liquidity_engine import LiquidityEngine
from .engines.liquidity_models import LiquidityConfig, LiquidityPoolState
from .engines.order_block import OrderBlockEngine
from .engines.order_block_behavior import OrderBlockBehaviorSnapshot, OrderBlockBehaviorTracker
from .engines.order_block_engine import OrderBlockConfig
from .structure_location_replay import CausalBarClock
from .targeting.adapters import fvg_engulfing_evidence, liquidity_evidence, order_block_evidence
from .targeting.models import TargetEvidence, TargetEvidenceSnapshot


@dataclass(frozen=True, slots=True)
class TargetEvidenceMTFReplay:
    symbol: str
    timeframes: tuple[str, ...]
    snapshots: Mapping[str, TargetEvidenceSnapshot]
    evidence: tuple[TargetEvidence, ...]
    liquidity_behavior: Mapping[str, LiquidityBehaviorSnapshot] | None = None
    order_block_behavior: Mapping[str, tuple[OrderBlockBehaviorSnapshot, ...]] | None = None

    def for_timeframe(self, timeframe: str) -> TargetEvidenceSnapshot:
        normalized = timeframe.strip().lower()
        try:
            return self.snapshots[normalized]
        except KeyError as error:
            raise KeyError(f"target evidence timeframe not replayed: {timeframe}") from error


class _BaseTargetEvidenceRunner:
    def __init__(self, store: ParquetOHLCVStore, *, clock: CausalBarClock | None = None) -> None:
        self.store = store
        self.clock = clock or CausalBarClock()

    def _inputs(
        self,
        symbol: str,
        timeframes: tuple[str, ...],
        input_snapshot: AnalysisInputSnapshot | None,
    ) -> tuple[str, tuple[str, ...], AnalysisInputSnapshot]:
        normalized_symbol = normalize_symbol(symbol)
        normalized_timeframes = normalize_timeframes(
            timeframes,
            supported=ANALYSIS_TIMEFRAMES,
            label="target evidence",
        )
        if input_snapshot is None:
            inputs = load_analysis_inputs(
                self.store,
                symbol=normalized_symbol,
                timeframes=normalized_timeframes,
            )
        else:
            input_snapshot.validate_request(
                symbol=normalized_symbol,
                timeframes=normalized_timeframes,
            )
            inputs = input_snapshot
        return normalized_symbol, normalized_timeframes, inputs

    @staticmethod
    def _atr(frame: pd.DataFrame, length: int = 14) -> float:
        tr_values: list[float] = []
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
            prev_close = close
        fallback = tr_values[-1] if tr_values else 0.01
        return max(float(atr if atr is not None else fallback), 1e-12)


class LiquidityMTFReplayRunner(_BaseTargetEvidenceRunner):
    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        config: LiquidityConfig | None = None,
        clock: CausalBarClock | None = None,
    ) -> None:
        super().__init__(store, clock=clock)
        self.config = config or LiquidityConfig()

    def replay(
        self,
        symbol: str,
        *,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> TargetEvidenceMTFReplay:
        normalized_symbol, normalized_timeframes, inputs = self._inputs(
            symbol, timeframes, input_snapshot
        )
        snapshots: dict[str, TargetEvidenceSnapshot] = {}
        behavior_by_timeframe: dict[str, LiquidityBehaviorSnapshot] = {}
        all_evidence: list[TargetEvidence] = []
        for timeframe in normalized_timeframes:
            frame = inputs.for_timeframe(timeframe).input_batch.frame
            engine = LiquidityEngine(self.config)
            confirmations: dict[str, tuple[object, object]] = {}
            for row in frame.to_dict("records"):
                engine.update(row)
                for pool in engine.pools:
                    if (
                        pool.identity not in confirmations
                        and pool.state in {LiquidityPoolState.ACTIVE, LiquidityPoolState.TESTED}
                    ):
                        confirmed_at = row["timestamp"]
                        confirmations[pool.identity] = (
                            confirmed_at,
                            self.clock.available_at(confirmed_at, timeframe),
                        )
            evidence = liquidity_evidence(
                symbol=normalized_symbol,
                timeframe=timeframe,
                engine=engine,
                confirmations=confirmations,
            )
            last = frame.iloc[-1]
            snapshot = TargetEvidenceSnapshot(
                symbol=normalized_symbol,
                timeframe=timeframe,
                as_of=last["timestamp"],
                available_at=self.clock.available_at(last["timestamp"], timeframe),
                current_price=float(last["close"]),
                atr=self._atr(frame, self.config.atr_length),
                evidence=evidence,
            )
            snapshots[timeframe] = snapshot
            behavior_by_timeframe[timeframe] = engine.behavior_snapshot
            all_evidence.extend(evidence)
        return TargetEvidenceMTFReplay(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            snapshots=snapshots,
            evidence=tuple(all_evidence),
            liquidity_behavior=behavior_by_timeframe,
        )


class OrderBlockMTFReplayRunner(_BaseTargetEvidenceRunner):
    def __init__(
        self,
        store: ParquetOHLCVStore,
        *,
        config: OrderBlockConfig | None = None,
        clock: CausalBarClock | None = None,
    ) -> None:
        super().__init__(store, clock=clock)
        self.config = config or OrderBlockConfig()

    def replay(
        self,
        symbol: str,
        *,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> TargetEvidenceMTFReplay:
        normalized_symbol, normalized_timeframes, inputs = self._inputs(
            symbol, timeframes, input_snapshot
        )
        snapshots: dict[str, TargetEvidenceSnapshot] = {}
        behavior_by_timeframe: dict[str, tuple[OrderBlockBehaviorSnapshot, ...]] = {}
        all_evidence: list[TargetEvidence] = []
        for timeframe in normalized_timeframes:
            frame = inputs.for_timeframe(timeframe).input_batch.frame
            engine = OrderBlockEngine(self.config)
            behavior = OrderBlockBehaviorTracker(self.config)
            confirmations: dict[str, tuple[object, object]] = {}
            latest_behavior: tuple[OrderBlockBehaviorSnapshot, ...] = ()
            for bar_index, row in enumerate(frame.to_dict("records")):
                engine.update(row)
                latest_behavior = behavior.update(
                    engine.records,
                    bar_index=bar_index,
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
                            self.clock.available_at(confirmed_at, timeframe),
                        )
            evidence = order_block_evidence(
                symbol=normalized_symbol,
                timeframe=timeframe,
                engine=engine,
                confirmations=confirmations,
            )
            last = frame.iloc[-1]
            snapshot = TargetEvidenceSnapshot(
                symbol=normalized_symbol,
                timeframe=timeframe,
                as_of=last["timestamp"],
                available_at=self.clock.available_at(last["timestamp"], timeframe),
                current_price=float(last["close"]),
                atr=self._atr(frame),
                evidence=evidence,
            )
            snapshots[timeframe] = snapshot
            behavior_by_timeframe[timeframe] = latest_behavior
            all_evidence.extend(evidence)
        return TargetEvidenceMTFReplay(
            symbol=normalized_symbol,
            timeframes=normalized_timeframes,
            snapshots=snapshots,
            evidence=tuple(all_evidence),
            order_block_behavior=behavior_by_timeframe,
        )


class FvgEngulfingMTFReplayRunner(_BaseTargetEvidenceRunner):
    def replay(
        self,
        symbol: str,
        *,
        timeframes: tuple[str, ...] = ANALYSIS_TIMEFRAMES,
        input_snapshot: AnalysisInputSnapshot | None = None,
    ) -> TargetEvidenceMTFReplay:
        normalized_symbol, normalized_requested, inputs = self._inputs(
            symbol, timeframes, input_snapshot
        )
        supported = tuple(tf for tf in normalized_requested if tf in SUPPORTED_TIMEFRAMES)
        snapshots: dict[str, TargetEvidenceSnapshot] = {}
        all_evidence: list[TargetEvidence] = []
        for timeframe in supported:
            frame = inputs.for_timeframe(timeframe).input_batch.frame
            engine = FvgEngulfingEngine(FvgEngulfingConfig(timeframe=timeframe))
            confirmations: dict[str, tuple[object, object]] = {}
            for row in frame.to_dict("records"):
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
                            self.clock.available_at(confirmed_at, timeframe),
                        )
            evidence = fvg_engulfing_evidence(
                symbol=normalized_symbol,
                timeframe=timeframe,
                engine=engine,
                confirmations=confirmations,
            )
            last = frame.iloc[-1]
            snapshot = TargetEvidenceSnapshot(
                symbol=normalized_symbol,
                timeframe=timeframe,
                as_of=last["timestamp"],
                available_at=self.clock.available_at(last["timestamp"], timeframe),
                current_price=float(last["close"]),
                atr=self._atr(frame),
                evidence=evidence,
            )
            snapshots[timeframe] = snapshot
            all_evidence.extend(evidence)
        return TargetEvidenceMTFReplay(
            symbol=normalized_symbol,
            timeframes=supported,
            snapshots=snapshots,
            evidence=tuple(all_evidence),
        )


__all__ = [
    "FvgEngulfingMTFReplayRunner",
    "LiquidityMTFReplayRunner",
    "OrderBlockMTFReplayRunner",
    "TargetEvidenceMTFReplay",
]
