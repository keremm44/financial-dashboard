from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.reaction import assess_reaction, select_relevant_zones
from financial_dashboard.decision.structural import StructuralDirection

from .early_move_research import EarlyMoveAuditReport


@dataclass(frozen=True, slots=True)
class StructuralEventAge:
    event_type: str
    direction: int
    confirmed_at: Any | None
    available_at: Any | None
    age_hours: float | None
    age_1h_bars: int | None
    validity: str
    relevance: str
    bos_maturity: str


@dataclass(frozen=True, slots=True)
class BullishReactionEvidence:
    aggregate_state: str
    confirmation_present: bool
    failure_present: bool
    fvg_confirmed: tuple[str, ...]
    fvg_developing: tuple[str, ...]
    ob_favorable: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructureReactionCheckpoint:
    move_index: int
    move_pct: float
    atr_multiple: float
    reached_at: Any
    st_native_state: str | None
    st_direction: str | None
    st_thesis: str | None
    latest_bearish_external: StructuralEventAge | None
    latest_bullish_external: StructuralEventAge | None
    bullish_reaction: BullishReactionEvidence
    diagnostic: str


@dataclass(frozen=True, slots=True)
class StructureReactionAuditReport:
    symbol: str
    checkpoints: tuple[StructureReactionCheckpoint, ...]


def _enum_text(value: Any, default: str = "UNKNOWN") -> str:
    raw = getattr(value, "value", value)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def _snapshot_map(snapshots: Iterable[Any]) -> Mapping[pd.Timestamp, Any]:
    return {pd.Timestamp(snapshot.as_of): snapshot for snapshot in snapshots}


def _event_time(event: Any) -> pd.Timestamp | None:
    ref = getattr(event, "ref", None)
    if ref is None:
        return None
    value = getattr(ref, "available_at", None) or getattr(ref, "confirmed_at", None)
    if value is None:
        return None
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return None


def _external_directional_events(snapshot: Any, direction: int) -> tuple[Any, ...]:
    projection = getattr(snapshot, "structure", None)
    if projection is None:
        return ()
    try:
        row = projection.for_timeframe("1h")
    except (KeyError, AttributeError, TypeError):
        return ()
    rows = []
    for event in getattr(row, "events", ()):
        if str(getattr(event, "scope", "")).strip().upper() != "EXTERNAL":
            continue
        if str(getattr(event, "confirmation_status", "")).strip().upper() != "CONFIRMED":
            continue
        if int(getattr(event, "direction", 0)) != int(direction):
            continue
        rows.append(event)
    return tuple(rows)


def _event_age(event: Any | None, as_of: Any) -> StructuralEventAge | None:
    if event is None:
        return None
    timestamp = _event_time(event)
    age_hours = None
    age_bars = None
    if timestamp is not None:
        delta = pd.Timestamp(as_of) - timestamp
        if delta >= pd.Timedelta(0):
            age_hours = delta.total_seconds() / 3600.0
            age_bars = int(delta // pd.Timedelta(hours=1))
    ref = getattr(event, "ref", None)
    return StructuralEventAge(
        event_type=str(getattr(event, "event_type", "UNKNOWN")),
        direction=int(getattr(event, "direction", 0)),
        confirmed_at=None if ref is None else getattr(ref, "confirmed_at", None),
        available_at=None if ref is None else getattr(ref, "available_at", None),
        age_hours=age_hours,
        age_1h_bars=age_bars,
        validity=str(getattr(event, "validity", "UNKNOWN")),
        relevance=str(getattr(event, "relevance", "UNKNOWN")),
        bos_maturity=str(getattr(event, "bos_maturity", "UNKNOWN")),
    )


def _latest_event(snapshot: Any, direction: int, as_of: Any) -> StructuralEventAge | None:
    candidates = []
    for event in _external_directional_events(snapshot, direction):
        timestamp = _event_time(event)
        if timestamp is None or timestamp > pd.Timestamp(as_of):
            continue
        candidates.append((timestamp, event))
    if not candidates:
        return None
    _, event = max(candidates, key=lambda item: item[0])
    return _event_age(event, as_of)


def _bullish_fvg(snapshot: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    projection = getattr(snapshot, "fvg_engulfing_lifecycle", None)
    if projection is None:
        return (), ()
    confirmed: list[str] = []
    developing: list[str] = []
    for row in getattr(projection, "fvg", ()):
        if int(getattr(row, "direction", 0)) <= 0:
            continue
        if bool(getattr(row, "invalid", False)) or bool(getattr(row, "full_fill", False)):
            continue
        identity = str(getattr(row, "identity", "FVG"))
        evidence = int(getattr(row, "reaction_evidence_count", 0))
        if bool(getattr(row, "reaction_confirmed", False)):
            confirmed.append(f"{identity}:confirmed:evidence={evidence}")
        elif evidence > 0 or getattr(row, "first_test_index", None) is not None:
            developing.append(f"{identity}:developing:evidence={evidence}")
    return tuple(confirmed), tuple(developing)


def _bullish_ob(snapshot: Any) -> tuple[str, ...]:
    projection = getattr(snapshot, "order_block_behavior", None)
    if projection is None:
        return ()
    rows: list[str] = []
    for row in getattr(projection, "observations", ()):
        if not bool(getattr(row, "bullish", False)):
            continue
        favorable = float(getattr(row, "max_favorable_move_atr", 0.0) or 0.0)
        interaction = str(getattr(row, "interaction", "UNKNOWN"))
        active = bool(getattr(row, "active", False))
        visited = int(getattr(row, "visit_count", 0) or 0)
        if favorable <= 0.0 and visited <= 0 and not active:
            continue
        rows.append(
            f"{getattr(row, 'identity', 'OB')}:interaction={interaction}:"
            f"favorable={favorable:.2f}ATR:visits={visited}:active={active}"
        )
    return tuple(rows)


def _bullish_reaction(snapshot: Any, config: DecisionEngineConfig) -> BullishReactionEvidence:
    ob = getattr(snapshot, "order_block_behavior", None)
    fvg = getattr(snapshot, "fvg_engulfing_lifecycle", None)
    if config.reaction_relevance is not None:
        ob, fvg = select_relevant_zones(
            ob,
            fvg,
            current_price=getattr(snapshot, "current_price", None),
            policy=config.reaction_relevance,
        )
    assessment = assess_reaction(
        StructuralDirection.LONG,
        order_blocks=ob,
        fvg_engulfing=fvg,
        timeframes=("4h", "2h", "1h", "30m"),
        relevance=config.reaction_relevance,
    )
    confirmed, developing = _bullish_fvg(snapshot)
    return BullishReactionEvidence(
        aggregate_state=_enum_text(getattr(assessment, "state", None)),
        confirmation_present=bool(getattr(assessment, "confirmation_present", False)),
        failure_present=bool(getattr(assessment, "failure_present", False)),
        fvg_confirmed=confirmed,
        fvg_developing=developing,
        ob_favorable=_bullish_ob(snapshot),
    )


def _st_native(snapshot: Any) -> tuple[str | None, str | None, str | None]:
    projection = getattr(snapshot, "structure", None)
    if projection is None:
        return None, None, None
    try:
        row = projection.for_timeframe("1h")
    except (KeyError, AttributeError, TypeError):
        return None, None, None
    external = getattr(row, "external", None)
    if external is None:
        return None, None, None
    native = str(getattr(external, "state", "") or "") or None
    direction = int(getattr(external, "direction", 0))
    direction_text = "LONG" if direction > 0 else "SHORT" if direction < 0 else "UNRESOLVED"
    thesis = (
        "TRANSITIONING"
        if native and "TRANSITION" in native.upper()
        else "INTACT"
        if direction != 0
        else "UNRESOLVED"
    )
    return native, direction_text, thesis


def _diagnostic(
    *,
    st_direction: str | None,
    st_thesis: str | None,
    bearish: StructuralEventAge | None,
    bullish: StructuralEventAge | None,
    reaction: BullishReactionEvidence,
) -> str:
    bullish_reaction = reaction.confirmation_present or bool(reaction.fvg_confirmed) or bool(reaction.ob_favorable)
    bearish_age = None if bearish is None else bearish.age_1h_bars
    bullish_age = None if bullish is None else bullish.age_1h_bars
    if st_direction == "SHORT" and bullish_reaction and bearish_age is not None and bearish_age >= 12:
        return "STALE_BEARISH_STRUCTURE_WITH_BULLISH_REACTION_CANDIDATE"
    if st_direction == "SHORT" and st_thesis == "TRANSITIONING" and bullish_reaction:
        return "BEARISH_STRUCTURE_TRANSITIONING_WITH_BULLISH_REACTION"
    if st_direction == "SHORT" and bullish is not None and bearish is not None:
        if bullish_age is not None and bearish_age is not None and bullish_age < bearish_age:
            return "NEWER_BULLISH_EXTERNAL_EVENT_NOT_YET_OWNING_STATE"
    if st_direction == "SHORT":
        return "BEARISH_STRUCTURE_STILL_CURRENT_NO_STRONG_BULLISH_REACTION_PROOF"
    if bullish_reaction:
        return "BULLISH_STRUCTURE_OR_TRANSITION_WITH_REACTION_SUPPORT"
    return "NO_BULLISH_REACTION_PROOF_AT_CHECKPOINT"


def audit_structure_reaction_age(
    *,
    symbol: str,
    early_report: EarlyMoveAuditReport,
    snapshots: Iterable[Any],
    decision_config: DecisionEngineConfig | None = None,
) -> StructureReactionAuditReport:
    cfg = decision_config or DecisionEngineConfig()
    by_time = _snapshot_map(snapshots)
    rows: list[StructureReactionCheckpoint] = []
    for move_index, episode in enumerate(early_report.episodes, start=1):
        for checkpoint in episode.checkpoints:
            if checkpoint.reached_at is None:
                continue
            snapshot = by_time.get(pd.Timestamp(checkpoint.reached_at))
            if snapshot is None:
                continue
            native, direction, thesis = _st_native(snapshot)
            bearish = _latest_event(snapshot, -1, checkpoint.reached_at)
            bullish = _latest_event(snapshot, 1, checkpoint.reached_at)
            reaction = _bullish_reaction(snapshot, cfg)
            rows.append(
                StructureReactionCheckpoint(
                    move_index=move_index,
                    move_pct=float(episode.move.move_pct),
                    atr_multiple=float(checkpoint.atr_multiple),
                    reached_at=checkpoint.reached_at,
                    st_native_state=native,
                    st_direction=direction,
                    st_thesis=thesis,
                    latest_bearish_external=bearish,
                    latest_bullish_external=bullish,
                    bullish_reaction=reaction,
                    diagnostic=_diagnostic(
                        st_direction=direction,
                        st_thesis=thesis,
                        bearish=bearish,
                        bullish=bullish,
                        reaction=reaction,
                    ),
                )
            )
    return StructureReactionAuditReport(symbol=symbol, checkpoints=tuple(rows))


def _event_text(event: StructuralEventAge | None) -> str:
    if event is None:
        return "-"
    age = "-" if event.age_1h_bars is None else f"{event.age_1h_bars}x1h"
    when = event.available_at or event.confirmed_at or "-"
    return f"{event.event_type}@{when} age={age} validity={event.validity} relevance={event.relevance} maturity={event.bos_maturity}"


def _compact(values: tuple[str, ...], limit: int = 3) -> str:
    return "-" if not values else "; ".join(values[:limit])


def render_structure_reaction_text(report: StructureReactionAuditReport) -> str:
    lines = [
        "STRUCTURE AGE + BULLISH REACTION AUDIT (HINDSIGHT DIAGNOSTIC ONLY)",
        "------------------------------------------------------------------",
        "Decision authority: NONE",
        "",
    ]
    for row in report.checkpoints:
        reaction = row.bullish_reaction
        lines.append(
            f"#{row.move_index} {row.move_pct:+.2f}% @{row.atr_multiple:g} ATR {row.reached_at} | "
            f"ST={row.st_direction}/{row.st_thesis}/{row.st_native_state or '-'} | diagnostic={row.diagnostic}"
        )
        lines.append(f"  last bearish external: {_event_text(row.latest_bearish_external)}")
        lines.append(f"  last bullish external: {_event_text(row.latest_bullish_external)}")
        lines.append(
            f"  bullish reaction: state={reaction.aggregate_state} confirmed={reaction.confirmation_present} "
            f"failed={reaction.failure_present}"
        )
        lines.append(f"  FVG confirmed: {_compact(reaction.fvg_confirmed)}")
        lines.append(f"  FVG developing: {_compact(reaction.fvg_developing)}")
        lines.append(f"  OB favorable: {_compact(reaction.ob_favorable)}")
    return "\n".join(lines)


__all__ = [
    "BullishReactionEvidence",
    "StructuralEventAge",
    "StructureReactionAuditReport",
    "StructureReactionCheckpoint",
    "audit_structure_reaction_age",
    "render_structure_reaction_text",
]
