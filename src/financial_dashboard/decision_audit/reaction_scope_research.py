from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.context.envelope import ContextDataQuality, normalize_context_data_quality
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.reaction import assess_reaction, select_relevant_zones
from financial_dashboard.decision.structural import StructuralDirection

from .early_move_research import EarlyMoveAuditReport


@dataclass(frozen=True, slots=True)
class ReactionScopeCheckpoint:
    move_index: int
    move_pct: float
    atr_multiple: float
    reached_at: Any
    raw_bullish_fvg: int
    raw_confirmed_fvg: int
    relevant_bullish_fvg: int
    relevant_confirmed_fvg: int
    raw_bullish_ob: int
    raw_confirmed_ob: int
    relevant_bullish_ob: int
    relevant_confirmed_ob: int
    raw_valid_fvg: int
    relevant_valid_fvg: int
    raw_valid_ob: int
    relevant_valid_ob: int
    reaction_state: str
    reaction_confirmed: bool
    reaction_developing: bool
    reaction_failed: bool
    reaction_quality: str
    reaction_reasons: tuple[str, ...]
    diagnostic: str


@dataclass(frozen=True, slots=True)
class ReactionScopeAuditReport:
    symbol: str
    checkpoints: tuple[ReactionScopeCheckpoint, ...]


def _snapshot_map(snapshots: Iterable[Any]) -> Mapping[pd.Timestamp, Any]:
    return {pd.Timestamp(snapshot.as_of): snapshot for snapshot in snapshots}


def _enum_text(value: Any, default: str = "UNKNOWN") -> str:
    raw = getattr(value, "value", value)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def _valid(ref: Any) -> bool:
    if ref is None:
        return False
    return normalize_context_data_quality(getattr(ref, "data_quality", None)) is ContextDataQuality.VALID


def _bullish_fvg_counts(projection: Any | None) -> tuple[int, int, int]:
    if projection is None:
        return 0, 0, 0
    rows = [row for row in getattr(projection, "fvg", ()) if int(getattr(row, "direction", 0)) > 0]
    return (
        len(rows),
        sum(bool(getattr(row, "reaction_confirmed", False)) for row in rows),
        sum(_valid(getattr(row, "ref", None)) for row in rows),
    )


def _bullish_ob_counts(projection: Any | None) -> tuple[int, int, int]:
    if projection is None:
        return 0, 0, 0
    rows = [row for row in getattr(projection, "observations", ()) if bool(getattr(row, "bullish", False))]
    confirmed = 0
    for row in rows:
        state = str(getattr(row, "state", "")).strip().upper()
        interaction = str(getattr(row, "interaction", "")).strip().upper()
        if state == "REACTION_CONFIRMED" or interaction == "REACTION_CONFIRMED":
            confirmed += 1
    return len(rows), confirmed, sum(_valid(getattr(row, "ref", None)) for row in rows)


def _diagnostic(
    *,
    raw_fvg_confirmed: int,
    relevant_fvg_confirmed: int,
    raw_ob_confirmed: int,
    relevant_ob_confirmed: int,
    reaction_state: str,
    reaction_confirmed: bool,
) -> str:
    raw_confirmed = raw_fvg_confirmed + raw_ob_confirmed
    relevant_confirmed = relevant_fvg_confirmed + relevant_ob_confirmed
    if raw_confirmed > 0 and relevant_confirmed == 0:
        return "RAW_CONFIRMATION_FILTERED_OUT_BY_REACTION_RELEVANCE"
    if relevant_confirmed > 0 and not reaction_confirmed:
        return "RELEVANT_CONFIRMATION_NOT_AGGREGATED"
    if relevant_confirmed > 0 and reaction_confirmed:
        return "RELEVANT_CONFIRMATION_AGGREGATED_CORRECTLY"
    if reaction_state == "DEVELOPING":
        return "NO_CONFIRMED_ZONE_BUT_RELEVANT_DEVELOPING_REACTION"
    if reaction_state == "FAILED":
        return "REACTION_FAILURE_DOMINATES_WITHOUT_CONFIRMATION"
    if reaction_state in {"ABSENT", "UNKNOWN"}:
        return "NO_RELEVANT_BULLISH_REACTION_CONFIRMATION"
    return "REACTION_SCOPE_OTHER"


def audit_reaction_scope(
    *,
    symbol: str,
    early_report: EarlyMoveAuditReport,
    snapshots: Iterable[Any],
    decision_config: DecisionEngineConfig | None = None,
) -> ReactionScopeAuditReport:
    cfg = decision_config or DecisionEngineConfig()
    by_time = _snapshot_map(snapshots)
    results: list[ReactionScopeCheckpoint] = []

    for move_index, episode in enumerate(early_report.episodes, start=1):
        for checkpoint in episode.checkpoints:
            if checkpoint.reached_at is None:
                continue
            snapshot = by_time.get(pd.Timestamp(checkpoint.reached_at))
            if snapshot is None:
                continue

            raw_ob = getattr(snapshot, "order_block_behavior", None)
            raw_fvg = getattr(snapshot, "fvg_engulfing_lifecycle", None)
            relevant_ob, relevant_fvg = raw_ob, raw_fvg
            if cfg.reaction_relevance is not None:
                relevant_ob, relevant_fvg = select_relevant_zones(
                    raw_ob,
                    raw_fvg,
                    current_price=float(getattr(snapshot, "current_price")),
                    policy=cfg.reaction_relevance,
                )

            assessment = assess_reaction(
                StructuralDirection.LONG,
                order_blocks=relevant_ob,
                fvg_engulfing=relevant_fvg,
                timeframes=("4h", "2h", "1h", "30m"),
                relevance=cfg.reaction_relevance,
            )

            raw_fvg_total, raw_fvg_confirmed, raw_fvg_valid = _bullish_fvg_counts(raw_fvg)
            rel_fvg_total, rel_fvg_confirmed, rel_fvg_valid = _bullish_fvg_counts(relevant_fvg)
            raw_ob_total, raw_ob_confirmed, raw_ob_valid = _bullish_ob_counts(raw_ob)
            rel_ob_total, rel_ob_confirmed, rel_ob_valid = _bullish_ob_counts(relevant_ob)
            state = _enum_text(getattr(assessment, "state", None))
            confirmed = bool(getattr(assessment, "confirmation_present", False))

            results.append(
                ReactionScopeCheckpoint(
                    move_index=move_index,
                    move_pct=float(episode.move.move_pct),
                    atr_multiple=float(checkpoint.atr_multiple),
                    reached_at=checkpoint.reached_at,
                    raw_bullish_fvg=raw_fvg_total,
                    raw_confirmed_fvg=raw_fvg_confirmed,
                    relevant_bullish_fvg=rel_fvg_total,
                    relevant_confirmed_fvg=rel_fvg_confirmed,
                    raw_bullish_ob=raw_ob_total,
                    raw_confirmed_ob=raw_ob_confirmed,
                    relevant_bullish_ob=rel_ob_total,
                    relevant_confirmed_ob=rel_ob_confirmed,
                    raw_valid_fvg=raw_fvg_valid,
                    relevant_valid_fvg=rel_fvg_valid,
                    raw_valid_ob=raw_ob_valid,
                    relevant_valid_ob=rel_ob_valid,
                    reaction_state=state,
                    reaction_confirmed=confirmed,
                    reaction_developing=bool(getattr(assessment, "developing_present", False)),
                    reaction_failed=bool(getattr(assessment, "failure_present", False)),
                    reaction_quality=_enum_text(getattr(assessment, "data_quality", None)),
                    reaction_reasons=tuple(getattr(assessment, "reasons", ())),
                    diagnostic=_diagnostic(
                        raw_fvg_confirmed=raw_fvg_confirmed,
                        relevant_fvg_confirmed=rel_fvg_confirmed,
                        raw_ob_confirmed=raw_ob_confirmed,
                        relevant_ob_confirmed=rel_ob_confirmed,
                        reaction_state=state,
                        reaction_confirmed=confirmed,
                    ),
                )
            )

    return ReactionScopeAuditReport(symbol=symbol, checkpoints=tuple(results))


def _compact(values: tuple[str, ...], limit: int = 5) -> str:
    return "-" if not values else "; ".join(values[:limit])


def render_reaction_scope_text(report: ReactionScopeAuditReport) -> str:
    lines = [
        "REACTION RAW -> RELEVANT -> AGGREGATE AUDIT (HINDSIGHT DIAGNOSTIC ONLY)",
        "-----------------------------------------------------------------------",
        "Decision authority: NONE",
        "",
    ]
    for row in report.checkpoints:
        lines.append(
            f"#{row.move_index} {row.move_pct:+.2f}% @{row.atr_multiple:g} ATR {row.reached_at} | "
            f"reaction={row.reaction_state} confirmed={row.reaction_confirmed} "
            f"developing={row.reaction_developing} failed={row.reaction_failed} "
            f"quality={row.reaction_quality} | diagnostic={row.diagnostic}"
        )
        lines.append(
            "  FVG bullish: "
            f"raw={row.raw_bullish_fvg} confirmed={row.raw_confirmed_fvg} valid={row.raw_valid_fvg} -> "
            f"relevant={row.relevant_bullish_fvg} confirmed={row.relevant_confirmed_fvg} valid={row.relevant_valid_fvg}"
        )
        lines.append(
            "  OB bullish: "
            f"raw={row.raw_bullish_ob} confirmed={row.raw_confirmed_ob} valid={row.raw_valid_ob} -> "
            f"relevant={row.relevant_bullish_ob} confirmed={row.relevant_confirmed_ob} valid={row.relevant_valid_ob}"
        )
        lines.append(f"  aggregate reasons: {_compact(row.reaction_reasons)}")
    return "\n".join(lines)


__all__ = [
    "ReactionScopeAuditReport",
    "ReactionScopeCheckpoint",
    "audit_reaction_scope",
    "render_reaction_scope_text",
]
