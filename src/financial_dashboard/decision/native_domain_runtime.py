from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Mapping

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
from .history_source import _pattern_snapshot, _prefix_batch, _wilder_atr_history


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


class IncrementalNativeDomainRuntime:
    """Advance native domains exactly once per closed bar.

    This runtime owns the hot engine objects. ``freeze`` only materializes immutable
    read-models from their current causal state. The same object can therefore be
    driven by a historical event sweep or by live/catch-up bars.
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
        self._atrs = {
            timeframe: _wilder_atr_history(inputs.for_timeframe(timeframe).input_batch.frame)
            for timeframe in inputs.timeframes
        }
        self._liquidity_atrs = {
            timeframe: (
                self._atrs[timeframe]
                if liquidity_config.atr_length == 14
                else _wilder_atr_history(
                    inputs.for_timeframe(timeframe).input_batch.frame,
                    liquidity_config.atr_length,
                )
            )
            for timeframe in inputs.timeframes
        }
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

    def ingest(self, event: CausalBarEvent) -> None:
        runtime = self._runtimes[event.timeframe]
        row = dict(event.bar)
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

    def freeze(self, *, as_of: Any, watermarks: Mapping[str, int]) -> NativeDomainState:
        missing = tuple(tf for tf in self.inputs.timeframes if tf not in watermarks)
        if missing:
            raise ValueError(f"cannot freeze before every timeframe has causal state: {missing!r}")

        structure_replays: dict[str, StructureLocationTimeframeReplay] = {}
        pattern_snapshots: list[Any] = []
        liquidity_snapshots: dict[str, TargetEvidenceSnapshot] = {}
        liquidity_evidence_rows: list[Any] = []
        liquidity_behavior: dict[str, Any] = {}
        ob_snapshots: dict[str, TargetEvidenceSnapshot] = {}
        ob_evidence_rows: list[Any] = []
        ob_behavior: dict[str, tuple[Any, ...]] = {}
        fvg_snapshots: dict[str, TargetEvidenceSnapshot] = {}
        fvg_evidence_rows: list[Any] = []
        fvg_lifecycle: dict[str, tuple[Any, ...]] = {}
        engulfing_lifecycle: dict[str, tuple[Any, ...]] = {}

        for timeframe in self.inputs.timeframes:
            index = watermarks[timeframe]
            batch = self.inputs.for_timeframe(timeframe).input_batch
            prefix_batch = _prefix_batch(batch, index)
            runtime = self._runtimes[timeframe]
            row = batch.frame.iloc[index]

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
            structure_replays[timeframe] = StructureLocationTimeframeReplay(
                timeframe=timeframe,
                input_batch=prefix_batch,
                market_structure=market,
                support_resistance=support,
            )
            pattern_snapshots.append(
                _pattern_snapshot(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    bar_count=index + 1,
                    engine=runtime.pattern,
                )
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
            liquidity_snapshots[timeframe] = liq_snapshot
            liquidity_evidence_rows.extend(liq_evidence)
            liquidity_behavior[timeframe] = runtime.liquidity.behavior_snapshot

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
            ob_snapshots[timeframe] = ob_snapshot
            ob_evidence_rows.extend(ob_evidence)
            ob_behavior[timeframe] = runtime.latest_ob_behavior

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
                fvg_snapshots[timeframe] = fg_snapshot
                fvg_evidence_rows.extend(fg_evidence)
                fvg_lifecycle[timeframe] = FvgEngulfingMTFReplayRunner._fvg_snapshots(timeframe, runtime.fvg)
                engulfing_lifecycle[timeframe] = FvgEngulfingMTFReplayRunner._engulfing_snapshots(
                    timeframe,
                    runtime.fvg,
                )

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
            pattern_snapshots=tuple(pattern_snapshots),
        )
        liquidity = TargetEvidenceMTFReplay(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            snapshots=liquidity_snapshots,
            evidence=tuple(liquidity_evidence_rows),
            liquidity_behavior=liquidity_behavior,
        )
        order_block = TargetEvidenceMTFReplay(
            symbol=self.symbol,
            timeframes=tuple(self.inputs.timeframes),
            snapshots=ob_snapshots,
            evidence=tuple(ob_evidence_rows),
            order_block_behavior=ob_behavior,
        )
        fvg = (
            None
            if not fvg_snapshots
            else TargetEvidenceMTFReplay(
                symbol=self.symbol,
                timeframes=tuple(tf for tf in self.inputs.timeframes if tf in fvg_snapshots),
                snapshots=fvg_snapshots,
                evidence=tuple(fvg_evidence_rows),
                fvg_lifecycle=fvg_lifecycle,
                engulfing_lifecycle=engulfing_lifecycle,
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


__all__ = [
    "IncrementalNativeDomainRuntime",
    "NativeDomainState",
    "causal_bar_events",
]
