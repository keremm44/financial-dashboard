from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from financial_dashboard.decision.arbiter import assess_entry_arbitration
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.scenario import ScenarioPresence, ScenarioStage, assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon

from .research import LargeMarketMove


@dataclass(frozen=True, slots=True)
class ScenarioAuthorityMoveAudit:
    move: LargeMarketMove
    snapshots: int
    first_st_present_at: Any | None
    first_st_qualified_at: Any | None
    first_lt_present_at: Any | None
    first_lt_qualified_at: Any | None
    st_presence_counts: tuple[tuple[str, int], ...]
    st_stage_counts: tuple[tuple[str, int], ...]
    lt_presence_counts: tuple[tuple[str, int], ...]
    lt_stage_counts: tuple[tuple[str, int], ...]
    arbiter_selection_counts: tuple[tuple[str, int], ...]
    st_suppressed_by_lt_bars: int
    opportunity_absent_st_bars: int
    opportunity_unknown_st_bars: int
    top_st_waiting: tuple[tuple[str, int], ...]
    top_lt_waiting: tuple[tuple[str, int], ...]
    diagnosis: str


@dataclass(frozen=True, slots=True)
class ScenarioAuthorityAuditReport:
    symbol: str
    moves: tuple[ScenarioAuthorityMoveAudit, ...]


def _text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _counts(counter: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(counter.most_common())


def _top(counter: Counter[str], limit: int = 5) -> tuple[tuple[str, int], ...]:
    return tuple(counter.most_common(limit))


def _diagnosis(
    *,
    snapshots: int,
    st_present: int,
    st_qualified: int,
    lt_present: int,
    lt_qualified: int,
    suppressed: int,
    opportunity_absent: int,
    opportunity_unknown: int,
) -> str:
    if snapshots == 0:
        return "NO_CAUSAL_SNAPSHOTS"
    if st_present == 0:
        if opportunity_absent + opportunity_unknown >= max(1, snapshots // 3):
            return "ST_SCENARIO_NOT_FORMED_OPPORTUNITY_DOMINANT"
        return "ST_SCENARIO_NOT_FORMED"
    if st_qualified == 0:
        if suppressed > 0:
            return "ST_DEVELOPED_NOT_QUALIFIED_WITH_LT_PRIORITY"
        return "ST_PRESENT_BUT_NEVER_QUALIFIED"
    if suppressed >= max(1, st_qualified):
        return "QUALIFIED_ST_OFTEN_SUPPRESSED_BY_LT"
    if lt_present > 0 and lt_qualified > 0 and suppressed > 0:
        return "LT_PRIORITY_MATERIALLY_SUPPRESSED_ST"
    return "SCENARIO_AUTHORITY_NOT_PRIMARY_BOTTLENECK"


def audit_scenario_authority(
    *,
    symbol: str,
    moves: Iterable[LargeMarketMove],
    snapshots: Iterable[Any],
    decision_config: DecisionEngineConfig | None = None,
) -> ScenarioAuthorityAuditReport:
    """Post-hoc scenario/arbitration diagnostics for large UP moves only."""

    cfg = decision_config or DecisionEngineConfig()
    ordered = tuple(sorted(snapshots, key=lambda item: pd.Timestamp(item.as_of)))
    rows: list[ScenarioAuthorityMoveAudit] = []

    for move in moves:
        if move.direction != "UP":
            continue
        start, end = pd.Timestamp(move.start_time), pd.Timestamp(move.end_time)
        window = tuple(item for item in ordered if start <= pd.Timestamp(item.as_of) <= end)

        st_presence: Counter[str] = Counter()
        st_stage: Counter[str] = Counter()
        lt_presence: Counter[str] = Counter()
        lt_stage: Counter[str] = Counter()
        selections: Counter[str] = Counter()
        st_waiting: Counter[str] = Counter()
        lt_waiting: Counter[str] = Counter()
        first_st_present = None
        first_st_qualified = None
        first_lt_present = None
        first_lt_qualified = None
        suppressed = 0
        opportunity_absent = 0
        opportunity_unknown = 0

        for snapshot in window:
            try:
                lt = assess_entry_scenario(snapshot, DecisionHorizon.LONG_TERM, config=cfg)
                st = assess_entry_scenario(snapshot, DecisionHorizon.SHORT_TERM, config=cfg)
                arbitration = assess_entry_arbitration(snapshot, config=cfg, scenarios=(lt, st))
            except Exception:
                continue

            st_presence[_text(st.presence)] += 1
            st_stage[_text(st.stage)] += 1
            lt_presence[_text(lt.presence)] += 1
            lt_stage[_text(lt.stage)] += 1
            selections[_text(arbitration.selection)] += 1
            st_waiting.update(str(value) for value in st.waiting_for if value)
            lt_waiting.update(str(value) for value in lt.waiting_for if value)

            timestamp = snapshot.as_of
            if st.presence is ScenarioPresence.PRESENT and first_st_present is None:
                first_st_present = timestamp
            if st.presence is ScenarioPresence.PRESENT and st.stage is ScenarioStage.QUALIFIED and first_st_qualified is None:
                first_st_qualified = timestamp
            if lt.presence is ScenarioPresence.PRESENT and first_lt_present is None:
                first_lt_present = timestamp
            if lt.presence is ScenarioPresence.PRESENT and lt.stage is ScenarioStage.QUALIFIED and first_lt_qualified is None:
                first_lt_qualified = timestamp

            if DecisionHorizon.SHORT_TERM in arbitration.suppressed_horizons:
                suppressed += 1
            if st.presence is ScenarioPresence.ABSENT and _text(st.opportunity_state) == "NONE":
                opportunity_absent += 1
            if st.presence is ScenarioPresence.UNKNOWN and _text(st.unknown_reason) == "OPPORTUNITY_UNOBSERVED":
                opportunity_unknown += 1

        st_present_count = st_presence["PRESENT"]
        st_qualified_count = st_stage["QUALIFIED"]
        lt_present_count = lt_presence["PRESENT"]
        lt_qualified_count = lt_stage["QUALIFIED"]
        rows.append(
            ScenarioAuthorityMoveAudit(
                move=move,
                snapshots=len(window),
                first_st_present_at=first_st_present,
                first_st_qualified_at=first_st_qualified,
                first_lt_present_at=first_lt_present,
                first_lt_qualified_at=first_lt_qualified,
                st_presence_counts=_counts(st_presence),
                st_stage_counts=_counts(st_stage),
                lt_presence_counts=_counts(lt_presence),
                lt_stage_counts=_counts(lt_stage),
                arbiter_selection_counts=_counts(selections),
                st_suppressed_by_lt_bars=suppressed,
                opportunity_absent_st_bars=opportunity_absent,
                opportunity_unknown_st_bars=opportunity_unknown,
                top_st_waiting=_top(st_waiting),
                top_lt_waiting=_top(lt_waiting),
                diagnosis=_diagnosis(
                    snapshots=len(window),
                    st_present=st_present_count,
                    st_qualified=st_qualified_count,
                    lt_present=lt_present_count,
                    lt_qualified=lt_qualified_count,
                    suppressed=suppressed,
                    opportunity_absent=opportunity_absent,
                    opportunity_unknown=opportunity_unknown,
                ),
            )
        )

    return ScenarioAuthorityAuditReport(symbol=symbol, moves=tuple(rows))


def _fmt_counts(values: tuple[tuple[str, int], ...]) -> str:
    return "-" if not values else "; ".join(f"{name}={count}" for name, count in values)


def _fmt_time(value: Any | None) -> str:
    return "-" if value is None else str(value)


def render_scenario_authority_text(report: ScenarioAuthorityAuditReport) -> str:
    lines = [
        "SCENARIO / LT-ST AUTHORITY AUDIT (DIAGNOSTIC ONLY)",
        "--------------------------------------------------",
        "Decision authority: NONE",
    ]
    for index, row in enumerate(report.moves, start=1):
        move = row.move
        lines.extend(
            [
                "",
                f"#{index} UP {move.classification} {move.move_pct:+.2f}% {move.start_time} -> {move.end_time}",
                f"  ST presence: {_fmt_counts(row.st_presence_counts)} | stages: {_fmt_counts(row.st_stage_counts)}",
                f"  LT presence: {_fmt_counts(row.lt_presence_counts)} | stages: {_fmt_counts(row.lt_stage_counts)}",
                f"  arbiter: {_fmt_counts(row.arbiter_selection_counts)} | ST suppressed by LT={row.st_suppressed_by_lt_bars}",
                f"  first: ST present={_fmt_time(row.first_st_present_at)} | ST qualified={_fmt_time(row.first_st_qualified_at)} | LT qualified={_fmt_time(row.first_lt_qualified_at)}",
                f"  ST opportunity absence/unknown: {row.opportunity_absent_st_bars}/{row.opportunity_unknown_st_bars}",
                f"  ST waiting: {_fmt_counts(row.top_st_waiting)}",
                f"  LT waiting: {_fmt_counts(row.top_lt_waiting)}",
                f"  diagnosis: {row.diagnosis}",
            ]
        )
    return "\n".join(lines)


__all__ = [
    "ScenarioAuthorityAuditReport",
    "ScenarioAuthorityMoveAudit",
    "audit_scenario_authority",
    "render_scenario_authority_text",
]
