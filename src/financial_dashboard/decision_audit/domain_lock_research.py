from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.decision.engine import DecisionEngineConfig, assess_horizon_decision
from financial_dashboard.decision.scenario import assess_entry_scenario
from financial_dashboard.decision.structural import DecisionHorizon

from .early_move_research import EarlyMoveAuditReport


@dataclass(frozen=True, slots=True)
class DomainLockCheckpoint:
    atr_multiple: float
    reached_at: Any
    structure_direction: str
    thesis_state: str
    structure_quality: str
    permission_gate: str
    permission_scope: str
    permission_side: str
    durability: str
    reaction: str
    participation: str
    environment: str
    opportunity: str
    conflict: str
    timing: str
    eligibility: str
    scenario_presence: str
    scenario_stage: str
    lock_domains: tuple[str, ...]
    lock_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DomainLockEpisode:
    move_start: Any
    move_end: Any
    move_pct: float
    checkpoints: tuple[DomainLockCheckpoint, ...]


@dataclass(frozen=True, slots=True)
class DomainLockAuditReport:
    symbol: str
    episodes: tuple[DomainLockEpisode, ...]
    lock_counts: tuple[tuple[str, int], ...]


def _text(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text if text else default


def _state(value: Any, *attrs: str) -> str:
    for attr in attrs:
        candidate = getattr(value, attr, None)
        if candidate is not None:
            return _text(candidate)
    return "UNKNOWN"


def _classify_locks(assessment: Any, scenario: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    locks: list[str] = []
    evidence: list[str] = []

    direction = _text(assessment.structural.direction)
    thesis = _text(assessment.structural.thesis_state)
    quality = _text(assessment.structural.data_quality)
    presence = _text(scenario.presence)

    if direction != "LONG":
        locks.append("STRUCTURE")
        evidence.append(f"STRUCTURE_DIRECTION:{direction}")
    if thesis in {"INVALIDATED", "UNRESOLVED"}:
        locks.append("STRUCTURE")
        evidence.append(f"THESIS:{thesis}")
    if quality != "VALID":
        locks.append("STRUCTURE")
        evidence.append(f"STRUCTURE_QUALITY:{quality}")

    permission = assessment.permission
    permission_gate = _text(permission.gate_state)
    permission_side = _text(permission.permitted_side)
    if permission_gate in {"BLOCKED", "WAITING"} or permission_side not in {"LONG", "NONE"}:
        locks.append("PERMISSION")
        evidence.extend(str(item) for item in (*permission.blocking_reasons, *permission.waiting_for))

    blockers = tuple(str(item) for item in assessment.eligibility.blockers)
    waiting = tuple(str(item) for item in assessment.eligibility.waiting_for)
    tokens = (*blockers, *waiting, *tuple(str(item) for item in scenario.waiting_for))

    for token in tokens:
        upper = token.upper()
        if "STRUCTUR" in upper or "CANONICAL_STRUCTURAL" in upper:
            locks.append("STRUCTURE")
        elif "PERMISSION" in upper:
            locks.append("PERMISSION")
        elif "OPPORTUNITY" in upper or "DIRECTIONAL_ROOM" in upper or "TARGET_PATH" in upper:
            locks.append("TARGETING_OPPORTUNITY")
        elif "SETUP" in upper or "TIMING" in upper:
            locks.append("SETUP_TIMING")
        elif "CONFLICT" in upper:
            locks.append("CONFLICT")
        elif "VOLATILITY" in upper or "ENVIRONMENT" in upper:
            locks.append("VOLATILITY_ENVIRONMENT")
        elif "COVERAGE" in upper:
            locks.append("COVERAGE")
        evidence.append(token)

    if presence == "ABSENT" and not locks:
        locks.append("SCENARIO_EXISTENCE")
        evidence.extend(str(item) for item in scenario.reasons)
    elif presence == "UNKNOWN" and not locks:
        locks.append("SCENARIO_UNKNOWN")
        evidence.extend(str(item) for item in scenario.reasons)

    return tuple(dict.fromkeys(locks)), tuple(dict.fromkeys(item for item in evidence if item))


def audit_domain_locks(
    *,
    early_report: EarlyMoveAuditReport,
    snapshots: Iterable[Any],
    decision_config: DecisionEngineConfig | None = None,
) -> DomainLockAuditReport:
    """Explain which ST decision domains were restrictive at early-move checkpoints.

    Hindsight selects the already-known move/checkpoint only. Every domain value is
    recomputed from the exact frozen DecisionInput snapshot available at that time.
    The result has no decision authority.
    """

    cfg = decision_config or DecisionEngineConfig()
    snapshot_by_time: Mapping[pd.Timestamp, Any] = {
        pd.Timestamp(snapshot.as_of): snapshot for snapshot in snapshots
    }
    episodes: list[DomainLockEpisode] = []
    counts: Counter[str] = Counter()

    for episode in early_report.episodes:
        rows: list[DomainLockCheckpoint] = []
        for checkpoint in episode.checkpoints:
            if checkpoint.reached_at is None:
                continue
            snapshot = snapshot_by_time.get(pd.Timestamp(checkpoint.reached_at))
            if snapshot is None:
                continue
            assessment = assess_horizon_decision(
                snapshot,
                DecisionHorizon.SHORT_TERM,
                config=cfg,
                execution_event=None,
            )
            scenario = assess_entry_scenario(
                snapshot,
                DecisionHorizon.SHORT_TERM,
                config=cfg,
                assessment=assessment,
            )
            locks, evidence = _classify_locks(assessment, scenario)
            counts.update(locks)
            rows.append(
                DomainLockCheckpoint(
                    atr_multiple=checkpoint.atr_multiple,
                    reached_at=checkpoint.reached_at,
                    structure_direction=_text(assessment.structural.direction),
                    thesis_state=_text(assessment.structural.thesis_state),
                    structure_quality=_text(assessment.structural.data_quality),
                    permission_gate=_text(assessment.permission.gate_state),
                    permission_scope=_text(assessment.permission.scope),
                    permission_side=_text(assessment.permission.permitted_side),
                    durability=_state(assessment.durability, "state", "data_quality"),
                    reaction=_state(assessment.reaction, "state", "data_quality"),
                    participation=_state(assessment.participation, "state", "data_quality"),
                    environment=_state(assessment.environment, "risk", "state", "data_quality"),
                    opportunity=_state(assessment.opportunity, "state"),
                    conflict=_state(assessment.conflict, "state"),
                    timing=_state(assessment.timing, "state"),
                    eligibility=_state(assessment.eligibility, "state"),
                    scenario_presence=_text(scenario.presence),
                    scenario_stage=_text(scenario.stage),
                    lock_domains=locks,
                    lock_evidence=evidence,
                )
            )
        episodes.append(
            DomainLockEpisode(
                move_start=episode.move.start_time,
                move_end=episode.move.end_time,
                move_pct=episode.move.move_pct,
                checkpoints=tuple(rows),
            )
        )

    return DomainLockAuditReport(
        symbol=early_report.symbol,
        episodes=tuple(episodes),
        lock_counts=tuple(counts.most_common()),
    )


def _compact(values: tuple[str, ...], limit: int = 6) -> str:
    if not values:
        return "-"
    return "; ".join(values[:limit])


def render_domain_lock_text(report: DomainLockAuditReport) -> str:
    lines = [
        "EARLY MOVE DOMAIN LOCK AUDIT (HINDSIGHT DIAGNOSTIC ONLY)",
        "---------------------------------------------------------",
        "Decision authority: NONE",
        "Lock counts: " + (
            ", ".join(f"{name}={count}" for name, count in report.lock_counts)
            if report.lock_counts else "-"
        ),
        "",
    ]
    for index, episode in enumerate(report.episodes, start=1):
        lines.append(
            f"#{index} UP {episode.move_pct:+.2f}% {episode.move_start} -> {episode.move_end}"
        )
        for row in episode.checkpoints:
            lines.append(
                f"  @{row.atr_multiple:g} ATR {row.reached_at} | "
                f"scenario={row.scenario_presence}/{row.scenario_stage} "
                f"locks={_compact(row.lock_domains)}"
            )
            lines.append(
                "    Structure: "
                f"direction={row.structure_direction} thesis={row.thesis_state} quality={row.structure_quality}"
            )
            lines.append(
                "    Permission: "
                f"gate={row.permission_gate} side={row.permission_side} scope={row.permission_scope}"
            )
            lines.append(
                "    Domains: "
                f"stabil={row.durability} reaction={row.reaction} participation={row.participation} "
                f"volatility={row.environment} opportunity={row.opportunity} conflict={row.conflict} "
                f"timing={row.timing} eligibility={row.eligibility}"
            )
            lines.append(f"    lock evidence: {_compact(row.lock_evidence)}")
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = [
    "DomainLockAuditReport",
    "DomainLockCheckpoint",
    "DomainLockEpisode",
    "audit_domain_locks",
    "render_domain_lock_text",
]
