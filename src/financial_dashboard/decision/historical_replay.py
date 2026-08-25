from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction
from financial_dashboard.decision_audit import DecisionEvent, DecisionSide
from financial_dashboard.decision_input import build_decision_input_snapshot
from financial_dashboard.market_workspace import MarketAnalysisWorkspaceRunner
from financial_dashboard.structure_location_replay import CausalBarClock

from .composer import ActionPolicy, DecisionAction
from .engine import DecisionEngineConfig, HorizonDecisionAssessment, assess_horizon_decision
from .opportunity import OpportunityCalibration
from .structural import DecisionHorizon, StructuralDirection


class CausalCutoffStore(ParquetOHLCVStore):
    """Read-only view of an OHLCV cache at one historical knowledge cutoff.

    Repository files are never rewritten. ``load`` clips each timeframe to bars whose
    causal ``available_at`` is not later than the requested decision cutoff. This
    lets the existing workspace runner execute against historical information without
    giving it access to future bars.
    """

    def __init__(self, root: str | Path, *, cutoff: Any, clock: CausalBarClock | None = None) -> None:
        super().__init__(root)
        self.cutoff = pd.Timestamp(cutoff)
        self.clock = clock or CausalBarClock()

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        frame = super().load(symbol, timeframe)
        if frame.empty:
            return frame
        available = frame["timestamp"].map(lambda value: pd.Timestamp(self.clock.available_at(value, timeframe)))
        return frame.loc[available <= self.cutoff].reset_index(drop=True)

    def merge_and_save(self, frame: pd.DataFrame, *, symbol: str, timeframe: str, source: str) -> pd.DataFrame:
        raise RuntimeError("CausalCutoffStore is read-only")


@dataclass(frozen=True, slots=True)
class HistoricalReplayConfig:
    horizon: DecisionHorizon = DecisionHorizon.SHORT_TERM
    decision_timeframe: str = "1h"
    pattern_profile: str | None = None
    opportunity_calibration: OpportunityCalibration | None = None
    readiness_position_proxy: bool = False
    max_bars: int | None = None
    start_at: Any | None = None
    end_at: Any | None = None

    def __post_init__(self) -> None:
        if self.decision_timeframe.strip().lower() != "1h":
            raise ValueError("v1 causal historical replay evaluates decisions on 1h closes")
        if self.max_bars is not None and self.max_bars < 1:
            raise ValueError("max_bars must be >= 1 when provided")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _decision_side(side: StructuralDirection) -> DecisionSide:
    if side is StructuralDirection.LONG:
        return DecisionSide.LONG
    if side is StructuralDirection.SHORT:
        return DecisionSide.SHORT
    return DecisionSide.NONE


def _audit_action(action: DecisionAction) -> AuditDecisionAction:
    return AuditDecisionAction(action.value)


def _event_from_assessment(
    assessment: HorizonDecisionAssessment,
    *,
    price: float,
    action_override: AuditDecisionAction | None = None,
    proxy_reason: str | None = None,
) -> DecisionEvent:
    final = assessment.final
    action = action_override or _audit_action(final.action)
    reasons = final.reasons
    if proxy_reason is not None:
        reasons = (*reasons, proxy_reason)
    snapshot = {
        "historical_replay": True,
        "readiness_position_proxy": proxy_reason is not None,
        "horizon": assessment.horizon.value,
        "structural": _jsonable(assessment.structural),
        "relation": assessment.structural_snapshot.relation.value,
        "durability": _jsonable(assessment.durability),
        "reaction": _jsonable(assessment.reaction),
        "participation": _jsonable(assessment.participation),
        "environment": _jsonable(assessment.environment),
        "opportunity": _jsonable(assessment.opportunity),
        "coverage": _jsonable(assessment.coverage),
        "conflict": _jsonable(assessment.conflict),
        "timing": _jsonable(assessment.timing),
        "eligibility": _jsonable(assessment.eligibility),
        "execution": _jsonable(assessment.execution),
    }
    return DecisionEvent(
        timestamp=assessment.as_of,
        action=action,
        side=_decision_side(final.market_side),
        price=float(price),
        reasons=tuple(reasons),
        blockers=tuple(final.blockers),
        waiting_for=tuple(final.waiting_for),
        source_lineage=tuple(final.source_lineage),
        snapshot=snapshot,
    )


def apply_readiness_position_proxy(
    assessments: Iterable[tuple[HorizonDecisionAssessment, float]],
) -> tuple[DecisionEvent, ...]:
    """Convert READY side changes into long-only audit positions.

    This is deliberately an *audit proxy*, not production execution logic. It lets
    the historical audit grade entry/exit timing before a native fresh execution
    detector is calibrated. A flat account opens only on LONG READY. While a long is
    open, SHORT READY closes it. No short position is opened.
    """

    holding_long = False
    events: list[DecisionEvent] = []
    for assessment, price in assessments:
        final = assessment.final
        override: AuditDecisionAction | None = None
        proxy_reason: str | None = None
        if final.action is DecisionAction.READY:
            if not holding_long and final.market_side is StructuralDirection.LONG:
                holding_long = True
                override = AuditDecisionAction.BUY
                proxy_reason = "AUDIT_PROXY_LONG_ENTRY_FROM_READY"
            elif holding_long and final.market_side is StructuralDirection.SHORT:
                holding_long = False
                override = AuditDecisionAction.SELL
                proxy_reason = "AUDIT_PROXY_LONG_EXIT_FROM_OPPOSING_READY"
        events.append(
            _event_from_assessment(
                assessment,
                price=price,
                action_override=override,
                proxy_reason=proxy_reason,
            )
        )
    return tuple(events)


def replay_historical_decisions(
    cache_root: str | Path,
    *,
    symbol: str,
    config: HistoricalReplayConfig | None = None,
) -> tuple[DecisionEvent, ...]:
    """Replay the decision engine causally over historical 1h decision bars.

    Every workspace is built from a read-only cutoff store. This is intentionally
    correctness-first and may be slow; Tur 5 can optimize replay after the first
    trustworthy end-to-end audit is established.
    """

    cfg = config or HistoricalReplayConfig()
    base_store = ParquetOHLCVStore(cache_root)
    decision_tf = cfg.decision_timeframe.strip().lower()
    bars = base_store.load(symbol, decision_tf)
    if bars.empty:
        raise ValueError(f"no historical decision bars for {symbol} {decision_tf}")

    frame = bars.copy(deep=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
    if cfg.start_at is not None:
        frame = frame.loc[frame["timestamp"] >= pd.Timestamp(cfg.start_at)]
    if cfg.end_at is not None:
        frame = frame.loc[frame["timestamp"] <= pd.Timestamp(cfg.end_at)]
    if cfg.max_bars is not None:
        frame = frame.tail(cfg.max_bars)
    if frame.empty:
        return ()

    clock = CausalBarClock()
    action_policy = ActionPolicy(
        permitted_sides=(StructuralDirection.LONG, StructuralDirection.SHORT)
    )
    engine_config = DecisionEngineConfig(
        opportunity_calibration=cfg.opportunity_calibration,
        action_policy=action_policy,
    )

    assessments: list[tuple[HorizonDecisionAssessment, float]] = []
    for row in frame.itertuples(index=False):
        cutoff = clock.available_at(row.timestamp, decision_tf)
        cutoff_store = CausalCutoffStore(cache_root, cutoff=cutoff, clock=clock)
        workspace = MarketAnalysisWorkspaceRunner(cutoff_store).run(
            symbol=symbol,
            pattern_profile=cfg.pattern_profile,
        )
        snapshot = build_decision_input_snapshot(workspace)
        assessment = assess_horizon_decision(
            snapshot,
            cfg.horizon,
            config=engine_config,
            execution_event=None,
        )
        assessments.append((assessment, float(snapshot.current_price)))

    if cfg.readiness_position_proxy:
        return apply_readiness_position_proxy(assessments)
    return tuple(
        _event_from_assessment(assessment, price=price)
        for assessment, price in assessments
    )


__all__ = [
    "CausalCutoffStore",
    "HistoricalReplayConfig",
    "apply_readiness_position_proxy",
    "replay_historical_decisions",
]
