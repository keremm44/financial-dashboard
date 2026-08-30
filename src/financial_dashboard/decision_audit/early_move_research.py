from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon

from .models import DecisionEvent
from .research import LargeMarketMove


@dataclass(frozen=True, slots=True)
class EarlyMoveAuditConfig:
    """Hindsight-only checkpoints normalized by pre-move 4H volatility."""

    atr_period: int = 14
    atr_multiples: tuple[float, ...] = (0.75, 1.25, 2.0)

    def __post_init__(self) -> None:
        if self.atr_period < 2:
            raise ValueError("atr_period must be >= 2")
        values = tuple(float(value) for value in self.atr_multiples)
        if not values or any(value <= 0.0 for value in values):
            raise ValueError("atr_multiples must be positive and non-empty")
        if tuple(sorted(set(values))) != values:
            raise ValueError("atr_multiples must be sorted and unique")
        object.__setattr__(self, "atr_multiples", values)


@dataclass(frozen=True, slots=True)
class EarlyMoveCheckpoint:
    atr_multiple: float
    threshold_price: float | None
    reached_at: Any | None
    reached_price: float | None
    elapsed_move_pct: float | None
    action: str | None
    st_presence: str | None
    st_stage: str | None
    st_kind: str | None
    st_timing: str | None
    st_opportunity: str | None
    st_eligibility: str | None
    lt_presence: str | None
    lt_stage: str | None
    selected_horizon: str | None
    waiting_for: tuple[str, ...]
    blockers: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EarlyMoveEpisode:
    move: LargeMarketMove
    start_atr_4h: float | None
    atr_sample_count: int
    checkpoints: tuple[EarlyMoveCheckpoint, ...]


@dataclass(frozen=True, slots=True)
class EarlyMoveAuditReport:
    symbol: str
    atr_period: int
    atr_multiples: tuple[float, ...]
    episodes: tuple[EarlyMoveEpisode, ...]


def _enum_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return "UNKNOWN" if raw is None else str(raw)


def _prepare_4h(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"4h bars missing columns: {sorted(missing)}")
    bars = frame.copy(deep=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="raise")
    bars = bars.sort_values("timestamp", kind="stable").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    for column in ("high", "low", "close"):
        bars[column] = pd.to_numeric(bars[column], errors="raise").astype(float)
    return bars


def _true_range(bars: pd.DataFrame) -> pd.Series:
    previous_close = bars["close"].shift(1)
    return pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _pre_move_atr(bars: pd.DataFrame, start_time: Any, period: int) -> tuple[float | None, int]:
    """Freeze ATR from completed 4H bars strictly before the hindsight move starts."""

    prior = bars.loc[bars["timestamp"] < pd.Timestamp(start_time)].copy()
    if len(prior) < period + 1:
        return None, len(prior)
    tr = _true_range(prior).iloc[-period:]
    if tr.isna().any() or len(tr) < period:
        return None, len(tr)
    return float(tr.mean()), len(tr)


def _snapshot_map(snapshots: Iterable[Any]) -> Mapping[pd.Timestamp, Any]:
    return {pd.Timestamp(snapshot.as_of): snapshot for snapshot in snapshots}


def _event_payload(event: DecisionEvent) -> tuple[str | None, str | None]:
    entry = event.snapshot.get("entry_decision")
    if not isinstance(entry, Mapping):
        return None, None
    selected = entry.get("selected_horizon")
    trade = entry.get("trade_horizon")
    return None if selected is None else str(selected), None if trade is None else str(trade)


def _checkpoint(
    *,
    event: DecisionEvent,
    move: LargeMarketMove,
    multiple: float,
    threshold_price: float,
    snapshot: Any | None,
    decision_config: DecisionEngineConfig,
) -> EarlyMoveCheckpoint:
    st_presence = st_stage = st_kind = st_timing = st_opportunity = st_eligibility = None
    lt_presence = lt_stage = None
    if snapshot is not None:
        try:
            st = assess_horizon_decision(snapshot, DecisionHorizon.SHORT_TERM, config=decision_config, execution_event=None)
            st_scenario = assess_entry_scenario(
                snapshot,
                DecisionHorizon.SHORT_TERM,
                config=decision_config,
                assessment=st,
            )
            lt = assess_horizon_decision(snapshot, DecisionHorizon.LONG_TERM, config=decision_config, execution_event=None)
            lt_scenario = assess_entry_scenario(
                snapshot,
                DecisionHorizon.LONG_TERM,
                config=decision_config,
                assessment=lt,
            )
            st_presence = _enum_text(st_scenario.presence)
            st_stage = _enum_text(st_scenario.stage)
            st_kind = _enum_text(st_scenario.kind)
            st_timing = _enum_text(st.timing.state)
            st_opportunity = _enum_text(st.opportunity.state)
            st_eligibility = _enum_text(st.eligibility.state)
            lt_presence = _enum_text(lt_scenario.presence)
            lt_stage = _enum_text(lt_scenario.stage)
        except Exception:
            pass

    selected, trade = _event_payload(event)
    price = None if event.price is None else float(event.price)
    elapsed = None
    total = move.end_price - move.start_price
    if price is not None and total > 0.0:
        elapsed = max(0.0, min(100.0, (price - move.start_price) / total * 100.0))

    return EarlyMoveCheckpoint(
        atr_multiple=float(multiple),
        threshold_price=float(threshold_price),
        reached_at=event.timestamp,
        reached_price=price,
        elapsed_move_pct=elapsed,
        action=_enum_text(event.action),
        st_presence=st_presence,
        st_stage=st_stage,
        st_kind=st_kind,
        st_timing=st_timing,
        st_opportunity=st_opportunity,
        st_eligibility=st_eligibility,
        lt_presence=lt_presence,
        lt_stage=lt_stage,
        selected_horizon=trade or selected,
        waiting_for=tuple(event.waiting_for),
        blockers=tuple(event.blockers),
        reasons=tuple(event.reasons),
    )


def _missing_checkpoint(multiple: float, threshold_price: float | None) -> EarlyMoveCheckpoint:
    return EarlyMoveCheckpoint(
        atr_multiple=float(multiple),
        threshold_price=threshold_price,
        reached_at=None,
        reached_price=None,
        elapsed_move_pct=None,
        action=None,
        st_presence=None,
        st_stage=None,
        st_kind=None,
        st_timing=None,
        st_opportunity=None,
        st_eligibility=None,
        lt_presence=None,
        lt_stage=None,
        selected_horizon=None,
        waiting_for=(),
        blockers=(),
        reasons=(),
    )


def audit_early_move_states(
    *,
    symbol: str,
    moves: Iterable[LargeMarketMove],
    market_bars_4h: pd.DataFrame,
    decisions: Iterable[DecisionEvent],
    snapshots: Iterable[Any] = (),
    decision_config: DecisionEngineConfig | None = None,
    config: EarlyMoveAuditConfig | None = None,
) -> EarlyMoveAuditReport:
    """Inspect what the causal system said after volatility-normalized early progress.

    Move selection is hindsight-only, but every checkpoint uses only a decision event
    that actually existed at that timestamp. ATR is frozen from completed 4H bars
    strictly before the move start, so the checkpoint scale itself does not use the
    future move endpoint.
    """

    cfg = config or EarlyMoveAuditConfig()
    engine_cfg = decision_config or DecisionEngineConfig()
    bars = _prepare_4h(market_bars_4h)
    ordered_events = tuple(sorted(decisions, key=lambda item: pd.Timestamp(item.timestamp)))
    snapshots_by_time = _snapshot_map(snapshots)
    episodes: list[EarlyMoveEpisode] = []

    for move in moves:
        if move.direction != "UP":
            continue
        atr, sample_count = _pre_move_atr(bars, move.start_time, cfg.atr_period)
        checkpoints: list[EarlyMoveCheckpoint] = []
        for multiple in cfg.atr_multiples:
            threshold = None if atr is None else float(move.start_price + multiple * atr)
            if threshold is None:
                checkpoints.append(_missing_checkpoint(multiple, None))
                continue
            event = next(
                (
                    candidate
                    for candidate in ordered_events
                    if pd.Timestamp(candidate.timestamp) >= pd.Timestamp(move.start_time)
                    and pd.Timestamp(candidate.timestamp) <= pd.Timestamp(move.end_time)
                    and candidate.price is not None
                    and float(candidate.price) >= threshold
                ),
                None,
            )
            if event is None:
                checkpoints.append(_missing_checkpoint(multiple, threshold))
                continue
            checkpoints.append(
                _checkpoint(
                    event=event,
                    move=move,
                    multiple=multiple,
                    threshold_price=threshold,
                    snapshot=snapshots_by_time.get(pd.Timestamp(event.timestamp)),
                    decision_config=engine_cfg,
                )
            )
        episodes.append(
            EarlyMoveEpisode(
                move=move,
                start_atr_4h=atr,
                atr_sample_count=sample_count,
                checkpoints=tuple(checkpoints),
            )
        )

    return EarlyMoveAuditReport(
        symbol=symbol,
        atr_period=cfg.atr_period,
        atr_multiples=cfg.atr_multiples,
        episodes=tuple(episodes),
    )


def _compact(values: Sequence[str], limit: int = 4) -> str:
    if not values:
        return "-"
    return "; ".join(values[:limit])


def render_early_move_text(report: EarlyMoveAuditReport) -> str:
    lines = [
        "EARLY MOVE STATE AUDIT (HINDSIGHT DIAGNOSTIC ONLY)",
        "---------------------------------------------------",
        f"Scale: causal pre-move 4H ATR({report.atr_period}); checkpoints="
        + ", ".join(f"{value:g} ATR" for value in report.atr_multiples),
        "Decision authority: NONE",
        "",
    ]
    for index, episode in enumerate(report.episodes, start=1):
        move = episode.move
        atr_text = "-" if episode.start_atr_4h is None else f"{episode.start_atr_4h:.2f}"
        lines.append(
            f"#{index} {move.direction} {move.classification} {move.move_pct:+.2f}% "
            f"{move.start_time} -> {move.end_time} | start_ATR4H={atr_text}"
        )
        for row in episode.checkpoints:
            if row.reached_at is None:
                threshold = "-" if row.threshold_price is None else f"{row.threshold_price:.2f}"
                lines.append(f"  @{row.atr_multiple:g} ATR threshold={threshold}: NOT_REACHED/NO_CAUSAL_DECISION")
                continue
            lines.append(
                f"  @{row.atr_multiple:g} ATR {row.reached_at} price={row.reached_price:.2f} "
                f"move_elapsed={row.elapsed_move_pct:.1f}% | action={row.action} trade={row.selected_horizon or '-'}"
            )
            lines.append(
                "    ST: "
                f"scenario={row.st_presence}/{row.st_stage}/{row.st_kind} "
                f"timing={row.st_timing} opportunity={row.st_opportunity} eligibility={row.st_eligibility}"
            )
            lines.append(f"    LT: scenario={row.lt_presence}/{row.lt_stage}")
            lines.append(f"    waiting: {_compact(row.waiting_for)}")
            lines.append(f"    blockers: {_compact(row.blockers)}")
            lines.append(f"    reasons: {_compact(row.reasons)}")
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = [
    "EarlyMoveAuditConfig",
    "EarlyMoveAuditReport",
    "EarlyMoveCheckpoint",
    "EarlyMoveEpisode",
    "audit_early_move_states",
    "render_early_move_text",
]
