from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .composer import DecisionAction
from .lifecycle import PositionState
from .lifecycle_replay import CanonicalLifecycleReplayResult
from .st_exit_intent import STExitFamily
from .structural import DecisionHorizon


_HOLD_PREFIX = "ST_CANONICAL_ECONOMIC_HOLD:"
_HOLD_PROGRESS = "HOLD_PROGRESS"
_HOLD_CONTINUATION = "HOLD_CONTINUATION"
_HOLD_HEALTHY_BASE = "HOLD_HEALTHY_BASE"
_SAME_MOVEMENT_REASON = "ST_REENTRY_SAME_ECONOMIC_MOVEMENT"
_UNRESOLVED_CONTINUITY_REASON = "ST_REENTRY_SETUP_CONTINUITY_UNRESOLVED"
_NOVEL_SETUP_REASON = "ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED"
_NOVELTY_WAIT = "ST_REENTRY_NOVELTY_TO_ESTABLISH"


@dataclass(frozen=True, slots=True)
class STTradeBehaviorObservation:
    trade_id: str
    entry_at: Any
    exit_at: Any
    exit_family: STExitFamily
    entry_price: float
    exit_price: float
    peak_price: float
    mfe_absolute: float
    mfe_return: float
    realized_return: float
    giveback_absolute: float
    giveback_from_peak_fraction: float
    holding_seconds: float
    harvest_idle_seconds: float
    protective_delay_seconds: float
    progress_hold_rows: int
    continuation_hold_rows: int
    healthy_base_hold_rows: int
    premature_harvest_candidate: bool
    exit_after_healthy_correction_candidate: bool

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("ST behavior trade_id must be non-empty")
        for value in (self.entry_price, self.exit_price, self.peak_price):
            if not isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError("ST behavior prices must be positive finite values")
        for value in (
            self.holding_seconds,
            self.harvest_idle_seconds,
            self.protective_delay_seconds,
        ):
            if float(value) < 0.0:
                raise ValueError("ST behavior durations cannot be negative")
        if min(
            self.progress_hold_rows,
            self.continuation_hold_rows,
            self.healthy_base_hold_rows,
        ) < 0:
            raise ValueError("ST behavior hold counters cannot be negative")


@dataclass(frozen=True, slots=True)
class STCanonicalBehaviorMetrics:
    completed_trade_count: int
    profit_harvest_count: int
    protective_exit_count: int
    strong_continuation_hold_rows: int
    healthy_base_hold_rows: int
    premature_harvest_candidates: int
    exit_after_healthy_correction_candidates: int
    same_movement_blocks: int
    unresolved_continuity_blocks: int
    novel_setups_released: int
    novel_setups_executed: int
    novel_setups_waiting_execution: int
    novelty_policy_contradictions: int
    st_reentries_without_novelty: int
    mean_holding_seconds: float
    mean_flat_capital_seconds: float
    open_ended_flat_capital_seconds: float
    mean_harvest_idle_seconds: float
    mean_protective_delay_seconds: float
    mean_giveback_absolute: float
    mean_mfe_return: float
    mean_realized_return: float

    def __post_init__(self) -> None:
        counts = (
            self.completed_trade_count,
            self.profit_harvest_count,
            self.protective_exit_count,
            self.strong_continuation_hold_rows,
            self.healthy_base_hold_rows,
            self.premature_harvest_candidates,
            self.exit_after_healthy_correction_candidates,
            self.same_movement_blocks,
            self.unresolved_continuity_blocks,
            self.novel_setups_released,
            self.novel_setups_executed,
            self.novel_setups_waiting_execution,
            self.novelty_policy_contradictions,
            self.st_reentries_without_novelty,
        )
        if min(counts, default=0) < 0:
            raise ValueError("ST behavior metric counts cannot be negative")
        if self.profit_harvest_count + self.protective_exit_count != self.completed_trade_count:
            raise ValueError("completed ST trades must have one terminal exit family")
        if self.novel_setups_executed + self.novel_setups_waiting_execution > self.novel_setups_released:
            raise ValueError("novel setup metrics are internally inconsistent")
        for value in (
            self.mean_holding_seconds,
            self.mean_flat_capital_seconds,
            self.open_ended_flat_capital_seconds,
            self.mean_harvest_idle_seconds,
            self.mean_protective_delay_seconds,
            self.mean_giveback_absolute,
        ):
            if float(value) < 0.0:
                raise ValueError("ST behavior aggregate durations/giveback cannot be negative")


@dataclass(frozen=True, slots=True)
class STCanonicalBehaviorReport:
    source: str
    production_performance: bool
    row_count: int
    proxy_row_count: int
    trades: tuple[STTradeBehaviorObservation, ...]
    metrics: STCanonicalBehaviorMetrics

    def __post_init__(self) -> None:
        if self.source not in {"CANONICAL", "CANONICAL_READINESS_PROXY"}:
            raise ValueError("unsupported ST canonical validation source")
        if self.row_count < 0 or self.proxy_row_count < 0:
            raise ValueError("ST validation row counts cannot be negative")
        if self.source == "CANONICAL" and self.proxy_row_count:
            raise ValueError("production canonical report cannot contain readiness proxy rows")
        if self.source == "CANONICAL_READINESS_PROXY" and self.production_performance:
            raise ValueError("readiness proxy cannot be labeled production performance")


@dataclass(frozen=True, slots=True)
class STLegacyBehaviorSummary:
    source: str
    event_count: int
    action_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if self.source != "LEGACY":
            raise ValueError("legacy summary source must remain LEGACY")
        if self.event_count < 0 or any(count < 0 for _, count in self.action_counts):
            raise ValueError("legacy summary counts cannot be negative")


@dataclass(frozen=True, slots=True)
class STBehaviorValidationBundle:
    canonical: STCanonicalBehaviorReport
    readiness_proxy: STCanonicalBehaviorReport | None = None
    legacy: STLegacyBehaviorSummary | None = None

    def __post_init__(self) -> None:
        if self.canonical.source != "CANONICAL" or not self.canonical.production_performance:
            raise ValueError("validation bundle requires production canonical report")
        if self.readiness_proxy is not None:
            if self.readiness_proxy.source != "CANONICAL_READINESS_PROXY":
                raise ValueError("readiness comparison must remain explicitly separate")
            if self.readiness_proxy.production_performance:
                raise ValueError("readiness proxy cannot be production performance")


def _timestamp(value: Any) -> pd.Timestamp:
    if value is None:
        raise ValueError("ST behavior validation timestamp must be known")
    return pd.Timestamp(value)


def _seconds(later: Any, earlier: Any) -> float:
    value = float((_timestamp(later) - _timestamp(earlier)).total_seconds())
    if value < 0.0:
        raise ValueError("ST behavior validation cannot move backward in time")
    return value


def _mean(values: Sequence[float]) -> float:
    return 0.0 if not values else float(sum(values) / len(values))


def _st_metadata(state: Any) -> Any | None:
    metadata = getattr(state, "entry_metadata", None)
    if metadata is None or getattr(metadata, "entry_horizon", None) is not DecisionHorizon.SHORT_TERM:
        return None
    return metadata


def _reasons(decision: Any | None) -> tuple[str, ...]:
    return tuple(str(item) for item in getattr(decision, "reasons", ()) if str(item))


def _hold_state(decision: Any | None) -> str | None:
    for reason in _reasons(decision):
        if reason.startswith(_HOLD_PREFIX):
            return reason[len(_HOLD_PREFIX) :]
    return None


def _progress_count(state: Any) -> int:
    history = getattr(state, "st_economic_history", None)
    return len(getattr(history, "progress_events", ()) or ())


def _flatten_replays(
    replay_or_segments: CanonicalLifecycleReplayResult | Iterable[CanonicalLifecycleReplayResult],
) -> tuple[Any, ...]:
    if isinstance(replay_or_segments, CanonicalLifecycleReplayResult):
        segments = (replay_or_segments,)
    else:
        segments = tuple(replay_or_segments)
    if not segments:
        return ()

    rows: list[Any] = []
    expected_state = segments[0].initial_state
    previous_as_of: pd.Timestamp | None = None
    for segment in segments:
        if segment.initial_state != expected_state:
            raise ValueError("ST validation replay segments must form one contiguous lifecycle chain")
        for row in segment.rows:
            current_as_of = _timestamp(row.snapshot.as_of)
            if previous_as_of is not None and current_as_of <= previous_as_of:
                raise ValueError("ST validation replay rows must be strictly increasing")
            if row.previous_state != expected_state:
                raise ValueError("ST validation row state chain is not contiguous")
            rows.append(row)
            expected_state = row.current_state
            previous_as_of = current_as_of
        if expected_state != segment.final_state:
            raise ValueError("ST validation replay segment final state mismatch")
    return tuple(rows)


def _legacy_action_name(event: Any) -> str:
    action = getattr(event, "action", event)
    return str(getattr(action, "value", action))


def summarize_legacy_behavior(events: Iterable[Any]) -> STLegacyBehaviorSummary:
    values = tuple(events)
    counts = Counter(_legacy_action_name(event) for event in values)
    return STLegacyBehaviorSummary(
        source="LEGACY",
        event_count=len(values),
        action_counts=tuple(sorted(counts.items())),
    )


def _build_report(
    replay_or_segments: CanonicalLifecycleReplayResult | Iterable[CanonicalLifecycleReplayResult],
    *,
    allow_readiness_proxy: bool,
) -> STCanonicalBehaviorReport:
    rows = _flatten_replays(replay_or_segments)
    proxy_rows = sum(bool(getattr(row, "execution_proxy_used", False)) for row in rows)
    if proxy_rows and not allow_readiness_proxy:
        raise ValueError(
            "production canonical behavior validation cannot consume readiness proxy rows"
        )

    trades: list[STTradeBehaviorObservation] = []
    current: dict[str, Any] | None = None
    last_st_sell_at: Any | None = None
    flat_durations: list[float] = []
    strong_holds = healthy_base_holds = 0
    premature_candidates = correction_candidates = 0
    same_blocks = unresolved_blocks = 0
    novel_released = novel_executed = novel_waiting = contradictions = 0
    reentries_without_novelty = 0
    previous_st_hold_state: str | None = None

    for row in rows:
        snapshot = row.snapshot
        entry = getattr(row, "entry_decision", None)
        exit_decision = getattr(row, "exit_decision", None)

        if row.previous_state.position is PositionState.FLAT:
            entry_reasons = set(_reasons(entry))
            waiting = set(str(item) for item in getattr(entry, "waiting_for", ()) if str(item))
            if _SAME_MOVEMENT_REASON in entry_reasons:
                same_blocks += 1
            if _UNRESOLVED_CONTINUITY_REASON in entry_reasons:
                unresolved_blocks += 1
            if _NOVEL_SETUP_REASON in entry_reasons:
                novel_released += 1
                if getattr(entry, "action", None) is DecisionAction.BUY:
                    novel_executed += 1
                elif getattr(entry, "action", None) is DecisionAction.READY:
                    novel_waiting += 1
                if _NOVELTY_WAIT in waiting:
                    contradictions += 1

            if row.action is DecisionAction.BUY and _st_metadata(row.current_state) is not None:
                if last_st_sell_at is not None:
                    flat_durations.append(_seconds(snapshot.as_of, last_st_sell_at))
                if (
                    getattr(row.previous_state, "last_closed_st_movement", None) is not None
                    and _NOVEL_SETUP_REASON not in entry_reasons
                ):
                    reentries_without_novelty += 1
                metadata = row.current_state.entry_metadata
                current = {
                    "trade_id": str(row.current_state.trade_id),
                    "entry_at": metadata.entry_as_of,
                    "entry_price": float(metadata.entry_price),
                    "peak_price": float(snapshot.current_price),
                    "harvest_at": None,
                    "protective_at": None,
                    "progress_holds": 0,
                    "continuation_holds": 0,
                    "healthy_base_holds": 0,
                    "premature": False,
                }
                previous_st_hold_state = None
            continue

        if _st_metadata(row.previous_state) is None:
            continue

        if current is None:
            metadata = row.previous_state.entry_metadata
            current = {
                "trade_id": str(row.previous_state.trade_id),
                "entry_at": metadata.entry_as_of,
                "entry_price": float(metadata.entry_price),
                "peak_price": float(snapshot.current_price),
                "harvest_at": None,
                "protective_at": None,
                "progress_holds": 0,
                "continuation_holds": 0,
                "healthy_base_holds": 0,
                "premature": False,
            }

        current["peak_price"] = max(float(current["peak_price"]), float(snapshot.current_price))
        hold_state = _hold_state(exit_decision)
        if hold_state == _HOLD_PROGRESS:
            current["progress_holds"] += 1
            strong_holds += 1
        elif hold_state == _HOLD_CONTINUATION:
            current["continuation_holds"] += 1
            strong_holds += 1
        elif hold_state == _HOLD_HEALTHY_BASE:
            current["healthy_base_holds"] += 1
            healthy_base_holds += 1

        family = getattr(exit_decision, "economic_exit_family", None)
        if family is STExitFamily.PROFIT_HARVEST and current["harvest_at"] is None:
            current["harvest_at"] = snapshot.as_of
        if family is STExitFamily.PROTECTIVE_EXIT and current["protective_at"] is None:
            current["protective_at"] = snapshot.as_of

        progress_added = _progress_count(row.current_state) > _progress_count(row.previous_state)
        if family is STExitFamily.PROFIT_HARVEST and progress_added:
            current["premature"] = True

        if row.action is DecisionAction.SELL:
            closed = getattr(row.current_state, "last_closed_st_exit", None)
            exit_family = getattr(closed, "family", None) or family
            if exit_family not in {STExitFamily.PROFIT_HARVEST, STExitFamily.PROTECTIVE_EXIT}:
                raise ValueError("completed canonical ST trade requires explicit exit family")
            exit_at = snapshot.as_of
            exit_price = float(snapshot.current_price)
            entry_price = float(current["entry_price"])
            peak_price = max(float(current["peak_price"]), exit_price)
            peak_gain = max(0.0, peak_price - entry_price)
            giveback = max(0.0, peak_price - exit_price)
            correction_candidate = previous_st_hold_state in {
                _HOLD_CONTINUATION,
                _HOLD_HEALTHY_BASE,
            }
            observation = STTradeBehaviorObservation(
                trade_id=str(current["trade_id"]),
                entry_at=current["entry_at"],
                exit_at=exit_at,
                exit_family=exit_family,
                entry_price=entry_price,
                exit_price=exit_price,
                peak_price=peak_price,
                mfe_absolute=peak_gain,
                mfe_return=(peak_price / entry_price) - 1.0,
                realized_return=(exit_price / entry_price) - 1.0,
                giveback_absolute=giveback,
                giveback_from_peak_fraction=(0.0 if peak_gain <= 0.0 else giveback / peak_gain),
                holding_seconds=_seconds(exit_at, current["entry_at"]),
                harvest_idle_seconds=(
                    0.0
                    if current["harvest_at"] is None
                    else _seconds(exit_at, current["harvest_at"])
                ),
                protective_delay_seconds=(
                    0.0
                    if current["protective_at"] is None
                    else _seconds(exit_at, current["protective_at"])
                ),
                progress_hold_rows=int(current["progress_holds"]),
                continuation_hold_rows=int(current["continuation_holds"]),
                healthy_base_hold_rows=int(current["healthy_base_holds"]),
                premature_harvest_candidate=bool(current["premature"]),
                exit_after_healthy_correction_candidate=correction_candidate,
            )
            trades.append(observation)
            premature_candidates += int(observation.premature_harvest_candidate)
            correction_candidates += int(observation.exit_after_healthy_correction_candidate)
            last_st_sell_at = exit_at
            current = None
            previous_st_hold_state = None
        else:
            previous_st_hold_state = hold_state

    open_flat = 0.0
    if rows and last_st_sell_at is not None and rows[-1].current_state.position is PositionState.FLAT:
        open_flat = _seconds(rows[-1].snapshot.as_of, last_st_sell_at)

    harvest_count = sum(trade.exit_family is STExitFamily.PROFIT_HARVEST for trade in trades)
    protective_count = sum(trade.exit_family is STExitFamily.PROTECTIVE_EXIT for trade in trades)
    metrics = STCanonicalBehaviorMetrics(
        completed_trade_count=len(trades),
        profit_harvest_count=harvest_count,
        protective_exit_count=protective_count,
        strong_continuation_hold_rows=strong_holds,
        healthy_base_hold_rows=healthy_base_holds,
        premature_harvest_candidates=premature_candidates,
        exit_after_healthy_correction_candidates=correction_candidates,
        same_movement_blocks=same_blocks,
        unresolved_continuity_blocks=unresolved_blocks,
        novel_setups_released=novel_released,
        novel_setups_executed=novel_executed,
        novel_setups_waiting_execution=novel_waiting,
        novelty_policy_contradictions=contradictions,
        st_reentries_without_novelty=reentries_without_novelty,
        mean_holding_seconds=_mean([trade.holding_seconds for trade in trades]),
        mean_flat_capital_seconds=_mean(flat_durations),
        open_ended_flat_capital_seconds=open_flat,
        mean_harvest_idle_seconds=_mean([trade.harvest_idle_seconds for trade in trades]),
        mean_protective_delay_seconds=_mean([trade.protective_delay_seconds for trade in trades]),
        mean_giveback_absolute=_mean([trade.giveback_absolute for trade in trades]),
        mean_mfe_return=_mean([trade.mfe_return for trade in trades]),
        mean_realized_return=_mean([trade.realized_return for trade in trades]),
    )
    return STCanonicalBehaviorReport(
        source="CANONICAL_READINESS_PROXY" if allow_readiness_proxy else "CANONICAL",
        production_performance=not allow_readiness_proxy,
        row_count=len(rows),
        proxy_row_count=proxy_rows,
        trades=tuple(trades),
        metrics=metrics,
    )


def validate_st_canonical_behavior(
    replay_or_segments: CanonicalLifecycleReplayResult | Iterable[CanonicalLifecycleReplayResult],
) -> STCanonicalBehaviorReport:
    """Measure production canonical ST behavior without mutating trading policy.

    Readiness-proxy rows are rejected so proxy output cannot be mislabeled as
    production execution performance. MFE, giveback and durations are analytics only.
    """

    return _build_report(replay_or_segments, allow_readiness_proxy=False)


def validate_st_readiness_proxy_behavior(
    replay_or_segments: CanonicalLifecycleReplayResult | Iterable[CanonicalLifecycleReplayResult],
) -> STCanonicalBehaviorReport:
    """Measure a readiness-proxy replay in an explicitly non-production section."""

    return _build_report(replay_or_segments, allow_readiness_proxy=True)


def build_st_behavior_validation_bundle(
    canonical_replay: CanonicalLifecycleReplayResult | Iterable[CanonicalLifecycleReplayResult],
    *,
    readiness_proxy_replay: CanonicalLifecycleReplayResult | Iterable[CanonicalLifecycleReplayResult] | None = None,
    legacy_events: Iterable[Any] | None = None,
) -> STBehaviorValidationBundle:
    return STBehaviorValidationBundle(
        canonical=validate_st_canonical_behavior(canonical_replay),
        readiness_proxy=(
            None
            if readiness_proxy_replay is None
            else validate_st_readiness_proxy_behavior(readiness_proxy_replay)
        ),
        legacy=(None if legacy_events is None else summarize_legacy_behavior(legacy_events)),
    )


__all__ = [
    "STBehaviorValidationBundle",
    "STCanonicalBehaviorMetrics",
    "STCanonicalBehaviorReport",
    "STLegacyBehaviorSummary",
    "STTradeBehaviorObservation",
    "build_st_behavior_validation_bundle",
    "summarize_legacy_behavior",
    "validate_st_canonical_behavior",
    "validate_st_readiness_proxy_behavior",
]
