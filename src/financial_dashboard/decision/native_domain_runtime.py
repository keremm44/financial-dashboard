from __future__ import annotations

from dataclasses import dataclass, replace
import pickle
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot
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
from financial_dashboard.mtf_replay import market_structure_timeframe_snapshot
from financial_dashboard.structure_location_replay import (
    CausalBarClock,
    StructureLocationMTFResult,
    StructureLocationTimeframeReplay,
    _support_snapshot,
)
from financial_dashboard.target_evidence_replay import FvgEngulfingMTFReplayRunner, TargetEvidenceMTFReplay
from financial_dashboard.targeting.adapters import (
    fvg_engulfing_evidence,
    liquidity_evidence,
    order_block_evidence,
)
from financial_dashboard.targeting.models import TargetEvidenceSnapshot

from .causal_reducer import CausalBarEvent
from .history_source import _pattern_snapshot, _wilder_atr_history


@dataclass(frozen=True, slots=True)
class NativeDomainState:
    symbol: str
    timeframes: tuple[str, ...]
    structure: StructureLocationMTFResult
    pattern: Any
    liquidity: TargetEvidenceMTFReplay | None
    order_block: TargetEvidenceMTFReplay | None
    fvg: TargetEvidenceMTFReplay | None


@dataclass(slots=True)
class _TimeframeRuntime:
    timeframe: str
    market: MarketStructureEngine
    support: RuntimeSupportResistanceRangeEngine
    pattern: RuntimePatternCompressionEngine
    liquidity: LiquidityEngine
    order_block: OrderBlockEngine
    order_block_behavior: OrderBlockBehaviorTracker
    fvg: FvgEngulfingEngine | None
    liquidity_confirmations: dict[str, tuple[object, object]]
    order_block_confirmations: dict[str, tuple[object, object]]
    fvg_confirmations: dict[str, tuple[object, object]]
    latest_ob_behavior: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class NativeRuntimeCheckpoint:
    """Continuation state for stateful native engines only.

    Read-model caches and historical DataFrame prefixes are deliberately excluded.
    On restore, current raw inputs provide the immutable price history while these
    engine objects provide the exact continuation state reached at the persisted
    reducer watermarks.
    """

    symbol: str
    timeframes: tuple[str, ...]
    runtimes: tuple[tuple[str, _TimeframeRuntime], ...]


@dataclass(frozen=True, slots=True)
class _FrozenTimeframeState:
    """Immutable native read-model for one exact timeframe watermark."""

    structure: StructureLocationTimeframeReplay
    pattern: Any
    liquidity_snapshot: TargetEvidenceSnapshot
    liquidity_evidence: tuple[Any, ...]
    liquidity_behavior: Any
    order_block_snapshot: TargetEvidenceSnapshot
    order_block_evidence: tuple[Any, ...]
    order_block_behavior: tuple[Any, ...]
    fvg_snapshot: TargetEvidenceSnapshot | None = None
    fvg_evidence: tuple[Any, ...] = ()
    fvg_lifecycle: tuple[Any, ...] = ()
    engulfing_lifecycle: tuple[Any, ...] = ()


def _true_ranges(frame: pd.DataFrame) -> list[float]:
    values: list[float] = []
    previous_close: float | None = None
    for row in frame.itertuples(index=False):
        high = float(row.high)
        low = float(row.low)
        close = float(row.close)
        tr = high - low
        if previous_close is not None:
            tr = max(tr, abs(high - previous_close), abs(low - previous_close))
        values.append(float(tr))
        previous_close = close
    return values


class IncrementalNativeDomainRuntime:
    """Advance native domains exactly once per closed bar.

    The hot engine objects are shared by cold historical replay and live catch-up.
    Existing cache bars reference the immutable prepared input frames. Bars arriving
    after that initial snapshot are appended to a small live extension and ATR state
    is extended with the same Wilder recurrence, so a running process never has to
    replay the old history merely to accept a new closed candle.

    ``freeze`` materializes immutable read-models and caches each timeframe by its
    exact watermark index. An unchanged 1d/4h/2h state is therefore not rebuilt just
    because a lower timeframe reached another decision cutoff.
    """

    def __init__(
        self,
        inputs: AnalysisInputSnapshot,
        *,
        symbol: str,
        clock: CausalBarClock | None = None,
        pattern_profile: str | None = None,
    ) -> None:
        self.inputs = inputs
        self.symbol = symbol
        self.clock = clock or CausalBarClock()
        pattern_config = None if pattern_profile is None else PatternCompressionConfig(profile=pattern_profile)
        liquidity_config = LiquidityConfig()
        order_block_config = OrderBlockConfig()
        self._liquidity_atr_length = int(liquidity_config.atr_length)
        self._atrs = {
            timeframe: list(_wilder_atr_history(inputs.for_timeframe(timeframe).input_batch.frame))
            for timeframe in inputs.timeframes
        }
        self._liquidity_atrs = {
            timeframe: (
                self._atrs[timeframe]
                if self._liquidity_atr_length == 14
                else list(
                    _wilder_atr_history(
                        inputs.for_timeframe(timeframe).input_batch.frame,
                        self._liquidity_atr_length,
                    )
                )
            )
            for timeframe in inputs.timeframes
        }
        self._true_ranges = {
            timeframe: _true_ranges(inputs.for_timeframe(timeframe).input_batch.frame)
            for timeframe in inputs.timeframes
        }
        self._extended_frames: dict[str, pd.DataFrame] = {}
        self._runtimes = {
            timeframe: _TimeframeRuntime(
                timeframe=timeframe,
                market=MarketStructureEngine(),
                support=RuntimeSupportResistanceRangeEngine(),
                pattern=RuntimePatternCompressionEngine(pattern_config),
                liquidity=LiquidityEngine(liquidity_config),
                order_block=OrderBlockEngine(order_block_config),
                order_block_behavior=OrderBlockBehaviorTracker(order_block_config),
                fvg=(
                    FvgEngulfingEngine(FvgEngulfingConfig(timeframe=timeframe))
                    if timeframe in SUPPORTED_TIMEFRAMES
                    else None
                ),
                liquidity_confirmations={},
                order_block_confirmations={},
                fvg_confirmations={},
                latest_ob_behavior=(),
            )
            for timeframe in inputs.timeframes
        }
        self._frozen_by_watermark: dict[tuple[str, int], _FrozenTimeframeState] = {}

    def export_checkpoint(self) -> NativeRuntimeCheckpoint:
        """Return a detached, pickle-verified snapshot of all stateful native engines."""

        detached = pickle.loads(
            pickle.dumps(
                tuple((timeframe, runtime) for timeframe, runtime in self._runtimes.items()),
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        )
        return NativeRuntimeCheckpoint(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            runtimes=detached,
        )

    def restore_checkpoint(self, checkpoint: NativeRuntimeCheckpoint) -> None:
        """Restore engine continuation state onto current append-compatible inputs."""

        if checkpoint.symbol != self.symbol:
            raise ValueError(
                f"native checkpoint symbol mismatch: {checkpoint.symbol!r} != {self.symbol!r}"
            )
        expected = tuple(self.inputs.timeframes)
        if checkpoint.timeframes != expected:
            raise ValueError(
                f"native checkpoint timeframe mismatch: {checkpoint.timeframes!r} != {expected!r}"
            )
        restored = dict(checkpoint.runtimes)
        if set(restored) != set(expected):
            raise ValueError("native checkpoint runtime set does not match requested timeframes")

        # Detach again so callers may safely reuse the checkpoint object in parity
        # tests without sharing mutable engine instances with the running reducer.
        self._runtimes = pickle.loads(
            pickle.dumps(restored, protocol=pickle.HIGHEST_PROTOCOL)
        )
        self._extended_frames.clear()
        self._frozen_by_watermark.clear()

    def _current_frame(self, timeframe: str) -> pd.DataFrame:
        return self._extended_frames.get(
            timeframe,
            self.inputs.for_timeframe(timeframe).input_batch.frame,
        )

    @staticmethod
    def _next_wilder_atr(
        *,
        tr_values: list[float],
        previous_values: list[float],
        length: int,
    ) -> float:
        count = len(tr_values)
        tr = float(tr_values[-1])
        if count < length:
            return max(tr, 1e-12)
        if count == length:
            return max(sum(tr_values[-length:]) / length, 1e-12)
        previous_atr = float(previous_values[-1])
        return max((previous_atr * (length - 1) + tr) / length, 1e-12)

    def _append_live_row(self, timeframe: str, index: int, row: Mapping[str, Any]) -> None:
        base = self.inputs.for_timeframe(timeframe).input_batch.frame
        current = self._current_frame(timeframe)
        if index < len(current):
            return
        if index != len(current):
            raise ValueError(
                f"cannot append non-contiguous live {timeframe} row: expected {len(current)}, got {index}"
            )

        previous_close = None if current.empty else float(current.iloc[-1]["close"])
        high = float(row["high"])
        low = float(row["low"])
        tr = high - low
        if previous_close is not None:
            tr = max(tr, abs(high - previous_close), abs(low - previous_close))
        self._true_ranges[timeframe].append(float(tr))

        atr_values = self._atrs[timeframe]
        atr_values.append(
            self._next_wilder_atr(
                tr_values=self._true_ranges[timeframe],
                previous_values=atr_values,
                length=14,
            )
        )
        if self._liquidity_atr_length != 14:
            liquidity_values = self._liquidity_atrs[timeframe]
            liquidity_values.append(
                self._next_wilder_atr(
                    tr_values=self._true_ranges[timeframe],
                    previous_values=liquidity_values,
                    length=self._liquidity_atr_length,
                )
            )

        incoming = pd.DataFrame([dict(row)])
        columns = tuple(base.columns)
        for column in columns:
            if column not in incoming.columns:
                incoming[column] = None
        incoming = incoming.loc[:, columns]
        self._extended_frames[timeframe] = pd.concat(
            [current, incoming],
            ignore_index=True,
        )

    def ingest(self, event: CausalBarEvent) -> None:
        runtime = self._runtimes[event.timeframe]
        row = dict(event.bar)
        self._append_live_row(event.timeframe, event.bar_index, row)

        runtime.market.update(row)
        runtime.support.update(row)
        runtime.pattern.update(row)

        runtime.liquidity.update(row)
        for pool in runtime.liquidity.pools:
            if pool.identity not in runtime.liquidity_confirmations and pool.state in {
                LiquidityPoolState.ACTIVE,
                LiquidityPoolState.TESTED,
            }:
                confirmed_at = row["timestamp"]
                runtime.liquidity_confirmations[pool.identity] = (
                    confirmed_at,
                    self.clock.available_at(confirmed_at, event.timeframe),
                )

        runtime.order_block.update(row)
        runtime.latest_ob_behavior = runtime.order_block_behavior.update(
            runtime.order_block.records,
            bar_index=event.bar_index,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )
        for record in runtime.order_block.records:
            identity = f"OB:{event.timeframe}:{record.source_index}:{1 if record.bullish else -1}"
            if identity not in runtime.order_block_confirmations and record.active:
                confirmed_at = row["timestamp"]
                runtime.order_block_confirmations[identity] = (
                    confirmed_at,
                    self.clock.available_at(confirmed_at, event.timeframe),
                )

        if runtime.fvg is not None:
            runtime.fvg.update(row)
            active_records = (
                runtime.fvg.active_bullish_fvg,
                runtime.fvg.active_bearish_fvg,
                runtime.fvg.active_bullish_engulfing,
                runtime.fvg.active_bearish_engulfing,
            )
            for record in active_records:
                if record is None:
                    continue
                prefix = "FVG" if hasattr(record, "gap_size") else "ENG"
                identity = f"{prefix}:{event.timeframe}:{record.formation_index}:{int(record.direction)}"
                if identity not in runtime.fvg_confirmations:
                    confirmed_at = row["timestamp"]
                    runtime.fvg_confirmations[identity] = (
                        confirmed_at,
                        self.clock.available_at(confirmed_at, event.timeframe),
                    )

    def _batch_at(self, timeframe: str, index: int):
        base_batch = self.inputs.for_timeframe(timeframe).input_batch
        frame = self._current_frame(timeframe)
        if index >= len(frame):
            raise IndexError(f"{timeframe} watermark {index} exceeds runtime frame length {len(frame)}")
        return replace(base_batch, frame=frame.iloc[: index + 1])

    def _freeze_timeframe(self, timeframe: str, index: int) -> _FrozenTimeframeState:
        cache_key = (timeframe, index)
        cached = self._frozen_by_watermark.get(cache_key)
        if cached is not None:
            return cached

        prefix_batch = self._batch_at(timeframe, index)
        runtime = self._runtimes[timeframe]
        row = prefix_batch.frame.iloc[-1]

        market = market_structure_timeframe_snapshot(
            symbol=self.symbol,
            timeframe=timeframe,
            batch=prefix_batch,
            engine=runtime.market,
        )
        support = _support_snapshot(
            symbol=self.symbol,
            timeframe=timeframe,
            batch=prefix_batch,
            engine=runtime.support,
            clock=self.clock,
        )
        structure = StructureLocationTimeframeReplay(
            timeframe=timeframe,
            input_batch=prefix_batch,
            market_structure=market,
            support_resistance=support,
        )
        pattern = _pattern_snapshot(
            symbol=self.symbol,
            timeframe=timeframe,
            bar_count=index + 1,
            engine=runtime.pattern,
        )

        liq_evidence = liquidity_evidence(
            symbol=self.symbol,
            timeframe=timeframe,
            engine=runtime.liquidity,
            confirmations=runtime.liquidity_confirmations,
        )
        liq_snapshot = TargetEvidenceSnapshot(
            symbol=self.symbol,
            timeframe=timeframe,
            as_of=row["timestamp"],
            available_at=self.clock.available_at(row["timestamp"], timeframe),
            current_price=float(row["close"]),
            atr=self._liquidity_atrs[timeframe][index],
            evidence=liq_evidence,
        )

        ob_evidence = order_block_evidence(
            symbol=self.symbol,
            timeframe=timeframe,
            engine=runtime.order_block,
            confirmations=runtime.order_block_confirmations,
        )
        behavior_state = {item.identity: item.state.value for item in runtime.latest_ob_behavior}
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
            symbol=self.symbol,
            timeframe=timeframe,
            as_of=row["timestamp"],
            available_at=self.clock.available_at(row["timestamp"], timeframe),
            current_price=float(row["close"]),
            atr=self._atrs[timeframe][index],
            evidence=ob_evidence,
        )

        fg_snapshot: TargetEvidenceSnapshot | None = None
        fg_evidence: tuple[Any, ...] = ()
        fvg_lifecycle: tuple[Any, ...] = ()
        engulfing_lifecycle: tuple[Any, ...] = ()
        if runtime.fvg is not None:
            fg_evidence = fvg_engulfing_evidence(
                symbol=self.symbol,
                timeframe=timeframe,
                engine=runtime.fvg,
                confirmations=runtime.fvg_confirmations,
            )
            fg_snapshot = TargetEvidenceSnapshot(
                symbol=self.symbol,
                timeframe=timeframe,
                as_of=row["timestamp"],
                available_at=self.clock.available_at(row["timestamp"], timeframe),
                current_price=float(row["close"]),
                atr=self._atrs[timeframe][index],
                evidence=fg_evidence,
            )
            fvg_lifecycle = FvgEngulfingMTFReplayRunner._fvg_snapshots(timeframe, runtime.fvg)
            engulfing_lifecycle = FvgEngulfingMTFReplayRunner._engulfing_snapshots(timeframe, runtime.fvg)

        frozen = _FrozenTimeframeState(
            structure=structure,
            pattern=pattern,
            liquidity_snapshot=liq_snapshot,
            liquidity_evidence=liq_evidence,
            liquidity_behavior=runtime.liquidity.behavior_snapshot,
            order_block_snapshot=ob_snapshot,
            order_block_evidence=ob_evidence,
            order_block_behavior=runtime.latest_ob_behavior,
            fvg_snapshot=fg_snapshot,
            fvg_evidence=fg_evidence,
            fvg_lifecycle=fvg_lifecycle,
            engulfing_lifecycle=engulfing_lifecycle,
        )
        self._frozen_by_watermark[cache_key] = frozen
        return frozen

    def freeze(self, *, as_of: Any, watermarks: Mapping[str, int]) -> NativeDomainState:
        missing = tuple(tf for tf in self.inputs.timeframes if tf not in watermarks)
        if missing:
            raise ValueError(f"cannot freeze before every timeframe has causal state: {missing!r}")

        frozen = {
            timeframe: self._freeze_timeframe(timeframe, int(watermarks[timeframe]))
            for timeframe in self.inputs.timeframes
        }
        structure_replays = {
            timeframe: state.structure for timeframe, state in frozen.items()
        }
        structure = StructureLocationMTFResult(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            replays=structure_replays,
            confluence=(),
            location_outcomes=(),
            event_zone_links=(),
        )
        pattern = SimpleNamespace(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            structure_location=structure,
            pattern_snapshots=tuple(frozen[tf].pattern for tf in self.inputs.timeframes),
        )
        liquidity = TargetEvidenceMTFReplay(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            snapshots={tf: frozen[tf].liquidity_snapshot for tf in self.inputs.timeframes},
            evidence=tuple(
                item
                for tf in self.inputs.timeframes
                for item in frozen[tf].liquidity_evidence
            ),
            liquidity_behavior={tf: frozen[tf].liquidity_behavior for tf in self.inputs.timeframes},
        )
        order_block = TargetEvidenceMTFReplay(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            snapshots={tf: frozen[tf].order_block_snapshot for tf in self.inputs.timeframes},
            evidence=tuple(
                item
                for tf in self.inputs.timeframes
                for item in frozen[tf].order_block_evidence
            ),
            order_block_behavior={tf: frozen[tf].order_block_behavior for tf in self.inputs.timeframes},
        )
        fvg_timeframes = tuple(
            tf for tf in self.inputs.timeframes if frozen[tf].fvg_snapshot is not None
        )
        fvg = (
            None
            if not fvg_timeframes
            else TargetEvidenceMTFReplay(
                symbol=self.symbol,
                timeframes=fvg_timeframes,
                snapshots={tf: frozen[tf].fvg_snapshot for tf in fvg_timeframes},
                evidence=tuple(
                    item
                    for tf in fvg_timeframes
                    for item in frozen[tf].fvg_evidence
                ),
                fvg_lifecycle={tf: frozen[tf].fvg_lifecycle for tf in fvg_timeframes},
                engulfing_lifecycle={tf: frozen[tf].engulfing_lifecycle for tf in fvg_timeframes},
            )
        )
        return NativeDomainState(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            structure=structure,
            pattern=pattern,
            liquidity=liquidity,
            order_block=order_block,
            fvg=fvg,
        )


def causal_bar_events(
    inputs: AnalysisInputSnapshot,
    *,
    clock: CausalBarClock | None = None,
) -> tuple[CausalBarEvent, ...]:
    active_clock = clock or CausalBarClock()
    events: list[CausalBarEvent] = []
    for timeframe in inputs.timeframes:
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        for index, row in enumerate(frame.to_dict("records")):
            events.append(
                CausalBarEvent(
                    available_at=active_clock.available_at(row["timestamp"], timeframe),
                    timeframe=timeframe,
                    bar_index=index,
                    bar=row,
                )
            )
    return tuple(sorted(events, key=lambda item: item.sort_key))


def causal_bar_events_after(
    inputs: AnalysisInputSnapshot,
    *,
    watermarks: Mapping[str, int],
    clock: CausalBarClock | None = None,
) -> tuple[CausalBarEvent, ...]:
    """Build only bars not already consumed by a restored reducer cursor."""

    active_clock = clock or CausalBarClock()
    events: list[CausalBarEvent] = []
    for timeframe in inputs.timeframes:
        if timeframe not in watermarks:
            raise ValueError(f"missing resume watermark for {timeframe}")
        frame = inputs.for_timeframe(timeframe).input_batch.frame
        start = int(watermarks[timeframe]) + 1
        if start < 0 or start > len(frame):
            raise ValueError(
                f"invalid resume watermark for {timeframe}: {watermarks[timeframe]}"
            )
        for offset, row in enumerate(frame.iloc[start:].to_dict("records"), start=start):
            events.append(
                CausalBarEvent(
                    available_at=active_clock.available_at(row["timestamp"], timeframe),
                    timeframe=timeframe,
                    bar_index=offset,
                    bar=row,
                )
            )
    return tuple(sorted(events, key=lambda item: item.sort_key))


__all__ = [
    "IncrementalNativeDomainRuntime",
    "NativeDomainState",
    "NativeRuntimeCheckpoint",
    "causal_bar_events",
    "causal_bar_events_after",
]
