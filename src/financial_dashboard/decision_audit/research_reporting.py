from __future__ import annotations

from dataclasses import asdict
from json import dumps
from typing import Any, Iterable

from .research import (
    BuySellResearchAuditReport,
    CounterfactualCheckpoint,
    HorizonStateDigest,
    LargeMoveAttribution,
    PatternStateDigest,
)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    raw = getattr(value, "value", None)
    return str(raw if raw is not None else value)


def render_research_json(report: BuySellResearchAuditReport) -> str:
    return dumps(asdict(report), ensure_ascii=False, indent=2, default=_json_default)


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _join(values: Iterable[str]) -> str:
    rows = tuple(str(value) for value in values if value)
    return "-" if not rows else "; ".join(rows)


def _counts(values: tuple[tuple[str, int], ...]) -> str:
    return "-" if not values else "; ".join(f"{name} x{count}" for name, count in values)


def _pattern(label: str, value: PatternStateDigest | None) -> str | None:
    if value is None:
        return None
    native = value.native_state or "-"
    return f"{label}: quality={value.quality} phase={value.phase} native={native}"


def _horizon(label: str, value: HorizonStateDigest | None) -> str | None:
    if value is None:
        return None
    if value.diagnostic_error is not None:
        return f"{label}: diagnostic_error={value.diagnostic_error}"
    return (
        f"{label}: structure={value.structural_direction}/{value.thesis_state} "
        f"scenario={value.scenario_presence}/{value.scenario_stage}/{value.scenario_kind} "
        f"timing={value.timing_state} reaction={value.reaction_state} "
        f"opportunity={value.opportunity_state} conflict={value.conflict_state} "
        f"eligibility={value.eligibility_state} no_exec_action={value.no_execution_action}"
    )


def _checkpoint_lines(checkpoint: CounterfactualCheckpoint) -> list[str]:
    prefix = f"  @{checkpoint.threshold_pct:g}% {checkpoint.relation}"
    if checkpoint.checkpoint_time is None:
        return [prefix + " | no causal decision snapshot at this threshold"]

    lines = [
        prefix
        + f" | {checkpoint.checkpoint_time} price={_fmt(checkpoint.checkpoint_price)}"
        + f" distance={_fmt(checkpoint.distance_from_extreme_pct)}%"
    ]
    lines.append(
        "    decision: "
        f"action={checkpoint.action or '-'} phase={checkpoint.lifecycle_phase or '-'} "
        f"scenario={checkpoint.scenario_stage or '-'}/{checkpoint.scenario_kind or '-'} "
        f"selected={checkpoint.selected_horizon or '-'} trade={checkpoint.trade_horizon or '-'} "
        f"execution={checkpoint.execution_state or '-'}"
    )
    if checkpoint.exit_stage is not None or checkpoint.position_health is not None:
        lines.append(
            "    exit: "
            f"stage={checkpoint.exit_stage or '-'} health={checkpoint.position_health or '-'}"
        )
    lines.append(f"    waiting_for: {_join(checkpoint.waiting_for)}")
    lines.append(f"    blockers: {_join(checkpoint.blockers)}")
    lines.append(f"    reasons: {_join(checkpoint.reasons)}")
    for text in (
        _pattern("pattern 1h", checkpoint.pattern_1h),
        _pattern("pattern 30m micro", checkpoint.pattern_30m),
        _horizon("ST", checkpoint.short_term),
        _horizon("LT", checkpoint.long_term),
    ):
        if text is not None:
            lines.append("    " + text)
    if checkpoint.short_term is not None:
        lines.append(
            "    ST scenario missing: waiting="
            + _join(checkpoint.short_term.scenario_waiting_for)
            + " | blockers="
            + _join(checkpoint.short_term.scenario_blockers)
        )
    if checkpoint.long_term is not None:
        lines.append(
            "    LT scenario missing: waiting="
            + _join(checkpoint.long_term.scenario_waiting_for)
            + " | blockers="
            + _join(checkpoint.long_term.scenario_blockers)
        )
    return lines


def _large_move_lines(index: int, row: LargeMoveAttribution) -> list[str]:
    move = row.move
    lines = [
        f"#{index} {move.direction} {move.classification} {move.move_pct:+.2f}% "
        f"{move.start_time} -> {move.end_time}",
        "  path: "
        f"{move.start_price:.2f} -> {move.end_price:.2f} | "
        f"{move.trading_days} trading days | {move.four_hour_bars} 4H bars | "
        f"{move.duration_hours:.1f}h | speed={move.move_pct_per_trading_day:.2f}%/day "
        f"({move.move_pct_per_4h_bar:.2f}%/4H bar)",
        f"  attribution: {row.status} | exposed_at_start={'YES' if row.exposed_at_start else 'NO'}",
    ]
    if row.action_time is not None:
        lines.append(
            "  action: "
            f"{row.action_time} price={_fmt(row.action_price)} horizon={row.action_horizon or '-'} | "
            f"move elapsed={_fmt(row.move_elapsed_before_action_pct)}% | "
            f"time elapsed={_fmt(row.time_elapsed_before_action_pct)}% | "
            f"remaining move={_fmt(row.remaining_move_after_action_pct)}%"
        )
    lines.append(f"  dominant waiting: {_counts(row.dominant_waiting_for)}")
    lines.append(f"  dominant blockers: {_counts(row.dominant_blockers)}")
    lines.append(f"  dominant non-action reasons: {_counts(row.dominant_reasons)}")
    return lines


def render_research_text(report: BuySellResearchAuditReport) -> str:
    lines = [
        "",
        "==================================================================",
        "BUY/SELL HINDSIGHT RESEARCH AUDIT (DIAGNOSTIC ONLY)",
        "==================================================================",
        f"Symbol: {report.symbol}",
        f"Counterfactual audit timeframe: {report.audit_timeframe}",
        f"Large-move market timeframe: {report.market_timeframe}",
        "Thresholds: " + ", ".join(f"{value:g}%" for value in report.thresholds_pct),
        "Decision authority: NONE (post-hoc only; no future data is fed back into BUY/SELL)",
        "",
        "PRE-ENTRY / PRE-EXIT COUNTERFACTUAL AUDIT",
        "-----------------------------------------",
    ]

    if not report.counterfactuals:
        lines.append("No executed BUY/SELL events to inspect.")
    else:
        for row in report.counterfactuals:
            lines.append(
                f"{row.action} {row.event_time} [{row.horizon}/{row.scenario_kind}] "
                f"price={row.event_price:.2f}"
            )
            lines.append(
                f"  hindsight {row.extreme_kind}: {row.extreme_time} price={row.extreme_price:.2f} | "
                f"event distance={row.event_distance_from_extreme_pct:.2f}% | {row.event_vs_extreme}"
            )
            for checkpoint in row.checkpoints:
                lines.extend(_checkpoint_lines(checkpoint))
            lines.append("")

    up_moves = [row for row in report.large_moves if row.move.direction == "UP"]
    down_moves = [row for row in report.large_moves if row.move.direction == "DOWN"]
    missed_up = sum(row.status == "MISSED_NO_BUY" for row in up_moves)
    caught_up = sum(row.status in {"BUY_CAPTURED", "ALREADY_LONG"} for row in up_moves)
    exposed_down = sum(row.status != "NOT_EXPOSED" for row in down_moves)

    lines.extend(
        [
            "4H LARGE MOVE AUDIT",
            "-------------------",
            f"Up moves >= threshold: {len(up_moves)} | captured/already long: {caught_up} | missed: {missed_up}",
            f"Down moves >= threshold: {len(down_moves)} | long exposure cases: {exposed_down}",
            "",
        ]
    )
    if not report.large_moves:
        lines.append("No qualifying large 4H moves in the decision sample.")
    else:
        for index, row in enumerate(report.large_moves, start=1):
            lines.extend(_large_move_lines(index, row))
            lines.append("")
    return "\n".join(lines).rstrip()


__all__ = ["render_research_json", "render_research_text"]
