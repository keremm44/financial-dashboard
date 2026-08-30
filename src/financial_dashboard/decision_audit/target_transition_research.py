from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.scenario import ScenarioPresence, ScenarioStage, assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection

from .models import DecisionAction, DecisionEvent
from .research import LargeMarketMove


_EPS = 1e-12
_REQUIRED_AUDIT_COLUMNS = {"timestamp", "low", "close"}


@dataclass(frozen=True, slots=True)
class TargetTransitionAuditConfig:
    """Post-hoc target-path timing diagnostics; never a decision policy."""

    persistence_snapshots: int = 2
    retest_tolerance_pct: float = 0.50
    top_n: int = 5
    max_episodes_per_move: int = 5

    def __post_init__(self) -> None:
        if self.persistence_snapshots < 1:
            raise ValueError("persistence_snapshots must be >= 1")
        if self.retest_tolerance_pct < 0.0:
            raise ValueError("retest_tolerance_pct must be >= 0")
        if self.top_n < 1:
            raise ValueError("top_n must be >= 1")
        if self.max_episodes_per_move < 1:
            raise ValueError("max_episodes_per_move must be >= 1")


@dataclass(frozen=True, slots=True)
class TargetPathNodeEpisodeAudit:
    identity: str
    roles: tuple[str, ...]
    sources: tuple[str, ...]
    native_states_at_first_seen: tuple[str, ...]
    first_seen_at: Any
    first_seen_price: float
    node_low: float
    node_high: float
    first_state: str
    price_cross_at: Any | None
    native_clear_at: Any | None
    persistence_at: Any | None
    retest_held_at: Any | None
    path_advanced_at: Any | None
    next_active_identity: str | None
    st_scenario_qualified_at: Any | None
    st_setup_ready_at: Any | None
    lt_scenario_qualified_at: Any | None
    buy_after_transition_at: Any | None
    buy_after_transition_price: float | None
    transition_to_buy_hours: float | None
    dominant_waiting_after_transition: tuple[tuple[str, int], ...]
    dominant_reasons_after_transition: tuple[tuple[str, int], ...]
    diagnostic_label: str


@dataclass(frozen=True, slots=True)
class LargeMoveTargetTransitionAudit:
    move: LargeMarketMove
    episodes: tuple[TargetPathNodeEpisodeAudit, ...]
    buy_time: Any | None
    buy_price: float | None


@dataclass(frozen=True, slots=True)
class TargetPathTransitionAuditReport:
    symbol: str
    market_timeframe: str
    decision_timeframe: str
    micro_retest_timeframe: str
    persistence_snapshots: int
    retest_tolerance_pct: float
    moves: tuple[LargeMoveTargetTransitionAudit, ...]


@dataclass(frozen=True, slots=True)
class _PathObservation:
    timestamp: pd.Timestamp
    price: float
    status: str
    active_identity: str | None
    active_low: float | None
    active_high: float | None
    active_state: str | None
    roles: tuple[str, ...]
    sources: tuple[str, ...]
    native_states: tuple[str, ...]
    native_disposition: str | None
    node_states: Mapping[str, tuple[str, str]]


def _enum_text(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text if text else default


def _prepare_audit_bars(frame: pd.DataFrame) -> pd.DataFrame:
    missing = _REQUIRED_AUDIT_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"target-transition audit bars missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("target-transition audit requires micro bars")
    bars = frame.copy(deep=True)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="raise")
    bars = (
        bars.sort_values("timestamp", kind="stable")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )
    for column in ("low", "close"):
        bars[column] = pd.to_numeric(bars[column], errors="raise").astype(float)
    return bars


def _snapshots_between(snapshots: Sequence[Any], start: Any, end: Any) -> tuple[Any, ...]:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    return tuple(
        item
        for item in snapshots
        if start_ts <= pd.Timestamp(item.as_of) <= end_ts
    )


def _events_between(events: Sequence[DecisionEvent], start: Any, end: Any) -> tuple[DecisionEvent, ...]:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    return tuple(
        event
        for event in events
        if start_ts <= pd.Timestamp(event.timestamp) <= end_ts
    )


def _top_tokens(events: Sequence[DecisionEvent], field: str, *, limit: int) -> tuple[tuple[str, int], ...]:
    counter: Counter[str] = Counter()
    for event in events:
        counter.update(str(value) for value in getattr(event, field) if value)
    return tuple(counter.most_common(limit))


def _path_observation(snapshot: Any) -> _PathObservation:
    path = snapshot.target_path(StructuralDirection.LONG)
    active = path.active_node
    node_states = {
        node.identity: (_enum_text(node.state), _enum_text(node.native_disposition))
        for node in path.nodes
    }
    return _PathObservation(
        timestamp=pd.Timestamp(snapshot.as_of),
        price=float(snapshot.current_price),
        status=_enum_text(path.status),
        active_identity=None if active is None else active.identity,
        active_low=None if active is None else float(active.low),
        active_high=None if active is None else float(active.high),
        active_state=None if active is None else _enum_text(active.state),
        roles=() if active is None else tuple(_enum_text(value) for value in active.roles),
        sources=() if active is None else tuple(_enum_text(value) for value in active.sources),
        native_states=() if active is None else tuple(str(value) for value in active.native_states),
        native_disposition=None if active is None else _enum_text(active.native_disposition),
        node_states=node_states,
    )


def _first_native_clear(observations: Sequence[_PathObservation], identity: str, start_index: int) -> Any | None:
    for row in observations[start_index:]:
        state = row.node_states.get(identity)
        if state is not None and (state[0] == "CLEARED" or state[1] == "CLEARED"):
            return row.timestamp
    return None


def _first_price_cross(
    observations: Sequence[_PathObservation],
    *,
    start_index: int,
    node_high: float,
) -> tuple[int | None, Any | None]:
    for index in range(start_index + 1, len(observations)):
        if observations[index].price > node_high:
            return index, observations[index].timestamp
    return None, None


def _persistence_at(
    observations: Sequence[_PathObservation],
    *,
    cross_index: int | None,
    node_high: float,
    required: int,
) -> Any | None:
    if cross_index is None:
        return None
    consecutive = 0
    for row in observations[cross_index:]:
        if row.price > node_high:
            consecutive += 1
            if consecutive >= required:
                return row.timestamp
        else:
            consecutive = 0
    return None


def _path_advance(
    observations: Sequence[_PathObservation],
    *,
    identity: str,
    start_index: int,
    node_high: float,
) -> tuple[Any | None, str | None]:
    for row in observations[start_index + 1 :]:
        if row.price <= node_high:
            continue
        if row.active_identity != identity:
            return row.timestamp, row.active_identity
    return None, None


def _held_retest(
    bars: pd.DataFrame,
    *,
    start: Any | None,
    end: Any,
    node_low: float,
    node_high: float,
    tolerance_pct: float,
) -> Any | None:
    if start is None:
        return None
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    window = bars[(bars["timestamp"] > start_ts) & (bars["timestamp"] <= end_ts)]
    if window.empty:
        return None
    upper_touch = node_high * (1.0 + tolerance_pct / 100.0)
    lower_floor = node_low * (1.0 - tolerance_pct / 100.0)
    matches = window[
        (window["low"] <= upper_touch)
        & (window["low"] >= lower_floor)
        & (window["close"] >= node_high)
    ]
    if matches.empty:
        return None
    return pd.Timestamp(matches.iloc[0]["timestamp"])


def _diagnostic_milestones(
    snapshots: Sequence[Any],
    *,
    start: Any,
    end: Any,
    decision_config: DecisionEngineConfig,
    cache: dict[tuple[pd.Timestamp, str], tuple[bool, bool]],
) -> tuple[Any | None, Any | None, Any | None]:
    st_qualified = None
    st_ready = None
    lt_qualified = None
    for snapshot in _snapshots_between(snapshots, start, end):
        timestamp = pd.Timestamp(snapshot.as_of)
        for horizon in (DecisionHorizon.SHORT_TERM, DecisionHorizon.LONG_TERM):
            key = (timestamp, horizon.value)
            value = cache.get(key)
            if value is None:
                try:
                    assessment = assess_horizon_decision(
                        snapshot,
                        horizon,
                        config=decision_config,
                        execution_event=None,
                    )
                    scenario = assess_entry_scenario(
                        snapshot,
                        horizon,
                        config=decision_config,
                        assessment=assessment,
                    )
                    qualified = (
                        scenario.presence is ScenarioPresence.PRESENT
                        and scenario.stage is ScenarioStage.QUALIFIED
                    )
                    ready = _enum_text(assessment.timing.state) == "READY"
                    value = (qualified, ready)
                except Exception:
                    value = (False, False)
                cache[key] = value
            qualified, ready = value
            if horizon is DecisionHorizon.SHORT_TERM:
                if st_qualified is None and qualified:
                    st_qualified = snapshot.as_of
                if st_ready is None and ready:
                    st_ready = snapshot.as_of
            elif lt_qualified is None and qualified:
                lt_qualified = snapshot.as_of
        if st_qualified is not None and st_ready is not None and lt_qualified is not None:
            break
    return st_qualified, st_ready, lt_qualified


def _transition_anchor(
    *,
    price_cross_at: Any | None,
    persistence_at: Any | None,
    path_advanced_at: Any | None,
) -> Any | None:
    return persistence_at or path_advanced_at or price_cross_at


def _hours_between(start: Any | None, end: Any | None) -> float | None:
    if start is None or end is None:
        return None
    delta = pd.Timestamp(end) - pd.Timestamp(start)
    return max(0.0, delta.total_seconds() / 3600.0)


def _label(
    *,
    price_cross_at: Any | None,
    persistence_at: Any | None,
    path_advanced_at: Any | None,
    st_qualified_at: Any | None,
    st_ready_at: Any | None,
    buy_at: Any | None,
) -> str:
    if price_cross_at is None:
        return "NODE_NOT_PRICE_CROSSED"
    if persistence_at is None:
        return "PRICE_CROSS_WITHOUT_PERSISTENCE"
    if path_advanced_at is None:
        return "PERSISTED_ABOVE_BUT_PATH_NOT_ADVANCED"
    if buy_at is not None:
        return "BUY_AFTER_TARGET_TRANSITION"
    if st_qualified_at is None:
        return "PATH_ADVANCED_NO_ST_QUALIFIED_SCENARIO"
    if st_ready_at is None:
        return "PATH_ADVANCED_ST_SCENARIO_SETUP_NOT_READY"
    return "PATH_ADVANCED_ST_READY_NO_BUY"


def _move_audit(
    move: LargeMarketMove,
    *,
    snapshots: Sequence[Any],
    events: Sequence[DecisionEvent],
    bars: pd.DataFrame,
    decision_config: DecisionEngineConfig,
    config: TargetTransitionAuditConfig,
    diagnostic_cache: dict[tuple[pd.Timestamp, str], tuple[bool, bool]],
) -> LargeMoveTargetTransitionAudit:
    move_snapshots = _snapshots_between(snapshots, move.start_time, move.end_time)
    observations = tuple(_path_observation(snapshot) for snapshot in move_snapshots)
    move_events = _events_between(events, move.start_time, move.end_time)
    buy = next((event for event in move_events if event.action is DecisionAction.BUY), None)

    episodes: list[TargetPathNodeEpisodeAudit] = []
    seen: set[str] = set()
    for start_index, row in enumerate(observations):
        identity = row.active_identity
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        if row.active_low is None or row.active_high is None or row.active_state is None:
            continue

        cross_index, price_cross_at = _first_price_cross(
            observations,
            start_index=start_index,
            node_high=row.active_high,
        )
        persistence_at = _persistence_at(
            observations,
            cross_index=cross_index,
            node_high=row.active_high,
            required=config.persistence_snapshots,
        )
        native_clear_at = _first_native_clear(observations, identity, start_index)
        path_advanced_at, next_active = _path_advance(
            observations,
            identity=identity,
            start_index=start_index,
            node_high=row.active_high,
        )
        retest_start = persistence_at or price_cross_at
        retest_at = _held_retest(
            bars,
            start=retest_start,
            end=move.end_time,
            node_low=row.active_low,
            node_high=row.active_high,
            tolerance_pct=config.retest_tolerance_pct,
        )
        anchor = _transition_anchor(
            price_cross_at=price_cross_at,
            persistence_at=persistence_at,
            path_advanced_at=path_advanced_at,
        )
        milestone_start = anchor or row.timestamp
        st_qualified, st_ready, lt_qualified = _diagnostic_milestones(
            snapshots,
            start=milestone_start,
            end=move.end_time,
            decision_config=decision_config,
            cache=diagnostic_cache,
        )
        buy_after = None
        if anchor is not None:
            buy_after = next(
                (
                    event
                    for event in move_events
                    if event.action is DecisionAction.BUY
                    and pd.Timestamp(event.timestamp) >= pd.Timestamp(anchor)
                ),
                None,
            )
        evidence_end = move.end_time if buy_after is None else buy_after.timestamp
        evidence_events = () if anchor is None else _events_between(events, anchor, evidence_end)
        buy_price = None if buy_after is None or buy_after.price is None else float(buy_after.price)

        episodes.append(
            TargetPathNodeEpisodeAudit(
                identity=identity,
                roles=row.roles,
                sources=row.sources,
                native_states_at_first_seen=row.native_states,
                first_seen_at=row.timestamp,
                first_seen_price=row.price,
                node_low=row.active_low,
                node_high=row.active_high,
                first_state=row.active_state,
                price_cross_at=price_cross_at,
                native_clear_at=native_clear_at,
                persistence_at=persistence_at,
                retest_held_at=retest_at,
                path_advanced_at=path_advanced_at,
                next_active_identity=next_active,
                st_scenario_qualified_at=st_qualified,
                st_setup_ready_at=st_ready,
                lt_scenario_qualified_at=lt_qualified,
                buy_after_transition_at=None if buy_after is None else buy_after.timestamp,
                buy_after_transition_price=buy_price,
                transition_to_buy_hours=_hours_between(anchor, None if buy_after is None else buy_after.timestamp),
                dominant_waiting_after_transition=_top_tokens(
                    evidence_events,
                    "waiting_for",
                    limit=config.top_n,
                ),
                dominant_reasons_after_transition=_top_tokens(
                    evidence_events,
                    "reasons",
                    limit=config.top_n,
                ),
                diagnostic_label=_label(
                    price_cross_at=price_cross_at,
                    persistence_at=persistence_at,
                    path_advanced_at=path_advanced_at,
                    st_qualified_at=st_qualified,
                    st_ready_at=st_ready,
                    buy_at=None if buy_after is None else buy_after.timestamp,
                ),
            )
        )
        if len(episodes) >= config.max_episodes_per_move:
            break

    return LargeMoveTargetTransitionAudit(
        move=move,
        episodes=tuple(episodes),
        buy_time=None if buy is None else buy.timestamp,
        buy_price=None if buy is None or buy.price is None else float(buy.price),
    )


def audit_target_path_transitions(
    *,
    symbol: str,
    moves: Iterable[LargeMarketMove],
    snapshots: Iterable[Any],
    decisions: Iterable[DecisionEvent],
    micro_bars: pd.DataFrame,
    decision_config: DecisionEngineConfig | None = None,
    config: TargetTransitionAuditConfig | None = None,
) -> TargetPathTransitionAuditReport:
    """Explain target-path timing inside hindsight large UP moves.

    Price crossing, consecutive 1H persistence and 30m held-retest observations are
    research-derived labels. Native TargetPath CLEARED/DEFENDED states remain
    separately reported and are never rewritten by this audit.
    """

    cfg = config or TargetTransitionAuditConfig()
    engine_cfg = decision_config or DecisionEngineConfig()
    bars = _prepare_audit_bars(micro_bars)
    snapshot_rows = tuple(sorted(snapshots, key=lambda item: pd.Timestamp(item.as_of)))
    event_rows = tuple(sorted(decisions, key=lambda item: pd.Timestamp(item.timestamp)))
    diagnostic_cache: dict[tuple[pd.Timestamp, str], tuple[bool, bool]] = {}
    rows = []
    for move in moves:
        if move.direction != "UP":
            continue
        rows.append(
            _move_audit(
                move,
                snapshots=snapshot_rows,
                events=event_rows,
                bars=bars,
                decision_config=engine_cfg,
                config=cfg,
                diagnostic_cache=diagnostic_cache,
            )
        )
    return TargetPathTransitionAuditReport(
        symbol=symbol,
        market_timeframe="4h",
        decision_timeframe="1h",
        micro_retest_timeframe="30m",
        persistence_snapshots=cfg.persistence_snapshots,
        retest_tolerance_pct=cfg.retest_tolerance_pct,
        moves=tuple(rows),
    )


def _fmt_time(value: Any | None) -> str:
    return "-" if value is None else str(value)


def _fmt_counts(values: tuple[tuple[str, int], ...]) -> str:
    return "-" if not values else "; ".join(f"{name} x{count}" for name, count in values)


def render_target_path_transition_text(report: TargetPathTransitionAuditReport) -> str:
    lines = [
        "TARGET PATH TRANSITION AUDIT (HINDSIGHT DIAGNOSTIC ONLY)",
        "--------------------------------------------------------",
        (
            f"Research persistence: {report.persistence_snapshots} consecutive {report.decision_timeframe} "
            f"decision closes above the prior node"
        ),
        (
            f"Research held-retest: {report.micro_retest_timeframe} touch/reclaim within "
            f"{report.retest_tolerance_pct:.2f}% tolerance"
        ),
        "Decision authority: NONE",
    ]
    if not report.moves:
        lines.append("No qualifying UP large moves to inspect.")
        return "\n".join(lines)

    for index, row in enumerate(report.moves, start=1):
        move = row.move
        lines.append("")
        lines.append(
            f"#{index} UP {move.classification} {move.move_pct:+.2f}% "
            f"{move.start_time} -> {move.end_time} | BUY={_fmt_time(row.buy_time)}"
        )
        if not row.episodes:
            lines.append("  No active LONG target-path node observed inside this move.")
            continue
        for episode_index, episode in enumerate(row.episodes, start=1):
            lines.append(
                f"  node {episode_index}: {episode.identity} | {episode.node_low:.2f}-{episode.node_high:.2f} "
                f"roles={','.join(episode.roles) or '-'} sources={','.join(episode.sources) or '-'}"
            )
            lines.append(
                "    timeline: "
                f"seen={_fmt_time(episode.first_seen_at)} -> "
                f"price_cross={_fmt_time(episode.price_cross_at)} -> "
                f"native_clear={_fmt_time(episode.native_clear_at)} -> "
                f"persistence={_fmt_time(episode.persistence_at)} -> "
                f"retest_held={_fmt_time(episode.retest_held_at)} -> "
                f"path_advanced={_fmt_time(episode.path_advanced_at)}"
            )
            lines.append(
                "    decision after transition: "
                f"ST_qualified={_fmt_time(episode.st_scenario_qualified_at)} | "
                f"ST_setup_READY={_fmt_time(episode.st_setup_ready_at)} | "
                f"LT_qualified={_fmt_time(episode.lt_scenario_qualified_at)} | "
                f"BUY={_fmt_time(episode.buy_after_transition_at)} | "
                f"delay_to_BUY={('-' if episode.transition_to_buy_hours is None else f'{episode.transition_to_buy_hours:.1f}h')}"
            )
            lines.append(f"    next active node: {episode.next_active_identity or '-'}")
            lines.append(f"    waiting after transition: {_fmt_counts(episode.dominant_waiting_after_transition)}")
            lines.append(f"    non-action reasons after transition: {_fmt_counts(episode.dominant_reasons_after_transition)}")
            lines.append(f"    diagnostic: {episode.diagnostic_label}")
    return "\n".join(lines)


__all__ = [
    "LargeMoveTargetTransitionAudit",
    "TargetPathNodeEpisodeAudit",
    "TargetPathTransitionAuditConfig",
    "TargetPathTransitionAuditReport",
    "audit_target_path_transitions",
    "render_target_path_transition_text",
]
