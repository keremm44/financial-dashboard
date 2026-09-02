from __future__ import annotations

from dataclasses import dataclass, is_dataclass, replace
from enum import StrEnum
from math import isfinite
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Iterable

import pandas as pd

from financial_dashboard.context.envelope import ContextDomain, FactRef

from .composer import DecisionAction
from .scenario import ScenarioStage
from .st_exit_intent import STExitFamily
from .st_thesis_identity import (
    STDefendedAnchor,
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
    _pullback_candidate,
    _sr_candidate,
    _unique_refs,
)
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision.entry import EntryDecision
    from financial_dashboard.decision.lifecycle import TradeLifecycleState
    from financial_dashboard.decision_input import DecisionInputSnapshot


class STSetupContinuityState(StrEnum):
    NO_PRIOR_MOVEMENT = "NO_PRIOR_MOVEMENT"
    SAME_MOVEMENT = "SAME_MOVEMENT"
    NOVEL_SETUP = "NOVEL_SETUP"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class STMovementRiskBoundary:
    kind: str
    identity: str
    timeframe: str
    low: float
    high: float

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.identity.strip() or not self.timeframe.strip():
            raise ValueError("ST movement risk boundary identity fields must be non-empty")
        if not isfinite(float(self.low)) or not isfinite(float(self.high)):
            raise ValueError("ST movement risk boundary bounds must be finite")
        if float(self.low) > float(self.high):
            raise ValueError("ST movement risk boundary low cannot exceed high")


@dataclass(frozen=True, slots=True)
class STClosedMovementRecord:
    """Minimal restart-safe factual identity of the most recently closed ST movement.

    This record deliberately stores only causal facts that cannot be reconstructed
    after a restart from the current market snapshot. It is not a cooldown, a re-entry
    verdict, or a second market engine.
    """

    trade_id: str
    entry_as_of: Any
    exit_as_of: Any
    exit_family: STExitFamily
    thesis_family: STThesisFamily
    economic_mission: STEconomicMission
    initial_risk: STMovementRiskBoundary | None
    terminal_risk: STMovementRiskBoundary | None
    initial_target_identity: str | None
    last_progress_area_id: str | None = None
    last_progress_observed_at: Any | None = None
    mission_completed_target_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("closed ST movement trade_id must be non-empty")
        if self.entry_as_of is None or self.exit_as_of is None:
            raise ValueError("closed ST movement timestamps must be known")
        try:
            if pd.Timestamp(self.exit_as_of) < pd.Timestamp(self.entry_as_of):
                raise ValueError("closed ST movement exit cannot predate entry")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc) == "closed ST movement exit cannot predate entry":
                raise
            raise TypeError("closed ST movement timestamps must be comparable") from exc
        if not isinstance(self.exit_family, STExitFamily):
            raise ValueError("closed ST movement exit family is invalid")
        if not isinstance(self.thesis_family, STThesisFamily):
            raise ValueError("closed ST movement thesis family is invalid")
        if not isinstance(self.economic_mission, STEconomicMission):
            raise ValueError("closed ST movement economic mission is invalid")
        if (self.last_progress_area_id is None) != (self.last_progress_observed_at is None):
            raise ValueError("closed ST movement progress identity/time must appear together")
        if self.last_progress_area_id is not None and not self.last_progress_area_id.strip():
            raise ValueError("closed ST movement progress identity must be non-empty")
        if self.initial_target_identity is not None and not self.initial_target_identity.strip():
            raise ValueError("closed ST movement initial target identity must be non-empty")
        if (
            self.mission_completed_target_identity is not None
            and not self.mission_completed_target_identity.strip()
        ):
            raise ValueError("closed ST movement completed target identity must be non-empty")


@dataclass(frozen=True, slots=True)
class STSetupCandidate:
    family: STThesisFamily
    economic_mission: STEconomicMission
    defended_anchor: STDefendedAnchor | None
    target_identity: str | None
    source_refs: tuple[FactRef, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError("ST setup candidate requires reasons")
        if _unique_refs(self.source_refs) != self.source_refs:
            raise ValueError("ST setup candidate refs must be sorted and unique")
        if self.family is STThesisFamily.UNRESOLVED:
            if self.economic_mission is not STEconomicMission.UNRESOLVED:
                raise ValueError("unresolved ST setup cannot carry resolved mission")
            if self.defended_anchor is not None:
                raise ValueError("unresolved ST setup cannot carry defended anchor")
            return
        if self.economic_mission is STEconomicMission.UNRESOLVED:
            raise ValueError("resolved ST setup requires economic mission")
        if self.defended_anchor is None:
            raise ValueError("resolved ST setup requires defended anchor")
        if self.target_identity is None or not self.target_identity.strip():
            raise ValueError("resolved ST setup requires target identity")


@dataclass(frozen=True, slots=True)
class STSetupContinuityAssessment:
    state: STSetupContinuityState
    candidate: STSetupCandidate | None
    new_information: bool
    new_risk_boundary: bool
    new_economic_move: bool
    protective_reversal_confirmed: bool
    reasons: tuple[str, ...]

    @property
    def reentry_allowed(self) -> bool:
        return self.state in {
            STSetupContinuityState.NO_PRIOR_MOVEMENT,
            STSetupContinuityState.NOVEL_SETUP,
        }


@dataclass(frozen=True, slots=True)
class STReentryNoveltyMetrics:
    same_movement_blocks: int
    unresolved_blocks: int
    novel_setups_released: int
    novel_setups_executed: int
    novel_setups_waiting_execution: int

    def __post_init__(self) -> None:
        if min(
            self.same_movement_blocks,
            self.unresolved_blocks,
            self.novel_setups_released,
            self.novel_setups_executed,
            self.novel_setups_waiting_execution,
        ) < 0:
            raise ValueError("ST re-entry novelty metrics cannot be negative")
        if self.novel_setups_executed + self.novel_setups_waiting_execution > self.novel_setups_released:
            raise ValueError("ST re-entry novelty metrics must be internally consistent")


def _risk_from_anchor(anchor: Any | None) -> STMovementRiskBoundary | None:
    if anchor is None:
        return None
    kind = getattr(anchor, "kind", None)
    kind_value = kind.value if hasattr(kind, "value") else str(kind or "")
    return STMovementRiskBoundary(
        kind=kind_value,
        identity=str(anchor.identity),
        timeframe=str(anchor.timeframe),
        low=float(anchor.low),
        high=float(anchor.high),
    )


def build_closed_st_movement_record(
    state: "TradeLifecycleState",
    *,
    exit_as_of: Any,
    exit_family: STExitFamily,
) -> STClosedMovementRecord | None:
    """Freeze minimal movement facts at the actual ST close boundary."""

    metadata = state.entry_metadata
    if (
        metadata is None
        or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM
        or metadata.st_trade_memory is None
    ):
        return None

    memory = metadata.st_trade_memory
    history = state.st_economic_history
    initial_risk = _risk_from_anchor(memory.initial_defended_anchor)
    terminal_risk = initial_risk
    last_progress_area_id = None
    last_progress_observed_at = None
    completed_target = None

    if history is not None:
        earned = history.active_earned_defense
        if earned is not None:
            area = next(
                (
                    item
                    for item in history.accepted_areas
                    if item.event_id == earned.accepted_area_id
                ),
                None,
            )
            terminal_risk = STMovementRiskBoundary(
                kind="EARNED_DEFENSE",
                identity=earned.event_id,
                timeframe="1h" if area is None else area.timeframe,
                low=float(earned.low),
                high=float(earned.high),
            )
        if history.progress_events:
            progress = history.progress_events[-1]
            last_progress_area_id = progress.accepted_area_id
            last_progress_observed_at = progress.observed_at
        if history.mission_completion is not None:
            completed_target = history.mission_completion.target_identity

    target = memory.initial_target_context
    return STClosedMovementRecord(
        trade_id=str(state.trade_id),
        entry_as_of=metadata.entry_as_of,
        exit_as_of=exit_as_of,
        exit_family=exit_family,
        thesis_family=memory.thesis_family,
        economic_mission=memory.economic_mission,
        initial_risk=initial_risk,
        terminal_risk=terminal_risk,
        initial_target_identity=None if target is None else target.identity,
        last_progress_area_id=last_progress_area_id,
        last_progress_observed_at=last_progress_observed_at,
        mission_completed_target_identity=completed_target,
    )


def _qualified_st_scenario(entry: Any) -> Any | None:
    if getattr(entry, "selected_horizon", None) is not DecisionHorizon.SHORT_TERM:
        return None
    if getattr(entry, "scenario_stage", None) is not ScenarioStage.QUALIFIED:
        return None
    arbitration = getattr(entry, "arbitration", None)
    scenario = None if arbitration is None else getattr(arbitration, "selected_scenario", None)
    if scenario is None:
        return None
    if getattr(scenario, "horizon", None) is not DecisionHorizon.SHORT_TERM:
        return None
    if getattr(scenario, "stage", None) is not ScenarioStage.QUALIFIED:
        return None
    if getattr(scenario, "structural_direction", None) is not StructuralDirection.LONG:
        return None
    return scenario


def classify_qualified_st_setup_candidate(
    snapshot: "DecisionInputSnapshot",
    entry: Any,
) -> STSetupCandidate | None:
    """Classify a qualified ST setup before execution-event consumption.

    This intentionally reuses the canonical Step-1 thesis candidate builders while
    omitting the executed-BUY requirement. Execution event identity is not an input.
    """

    scenario = _qualified_st_scenario(entry)
    if scenario is None:
        return None

    target_identity = getattr(scenario, "active_target_identity", None)
    if target_identity is not None:
        target_identity = str(target_identity).strip() or None

    candidates = tuple(
        candidate
        for candidate in (
            _sr_candidate(snapshot),
            _pullback_candidate(snapshot, scenario),
        )
        if candidate is not None
    )
    refs = _unique_refs(ref for candidate in candidates for ref in candidate.anchor.source_refs)

    if target_identity is None:
        return STSetupCandidate(
            family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            defended_anchor=None,
            target_identity=None,
            source_refs=refs,
            reasons=("ST_REENTRY_TARGET_CONTEXT_UNRESOLVED",),
        )

    families = {candidate.family for candidate in candidates}
    if len(candidates) != 1 or len(families) != 1:
        reason = (
            "ST_REENTRY_SETUP_EVIDENCE_INSUFFICIENT"
            if not candidates
            else "ST_REENTRY_SETUP_FAMILY_AMBIGUOUS:"
            + ",".join(sorted(family.value for family in families))
        )
        return STSetupCandidate(
            family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            defended_anchor=None,
            target_identity=target_identity,
            source_refs=refs,
            reasons=(reason,),
        )

    candidate = candidates[0]
    return STSetupCandidate(
        family=candidate.family,
        economic_mission=candidate.mission,
        defended_anchor=candidate.anchor,
        target_identity=target_identity,
        source_refs=candidate.anchor.source_refs,
        reasons=(candidate.reason,),
    )


def _confirmed_after(ref: FactRef, boundary: Any) -> bool:
    confirmed_at = getattr(ref, "confirmed_at", None)
    if confirmed_at is None:
        return False
    try:
        return pd.Timestamp(confirmed_at) > pd.Timestamp(boundary)
    except (TypeError, ValueError):
        return False


def _new_risk_boundary(
    candidate: STDefendedAnchor | None,
    previous: STMovementRiskBoundary | None,
    *,
    has_new_information: bool,
) -> bool:
    if candidate is None:
        return False
    if previous is None:
        return has_new_information
    candidate_kind = candidate.kind.value
    return (
        candidate.identity != previous.identity
        and (
            candidate_kind,
            candidate.timeframe,
            float(candidate.low),
            float(candidate.high),
        )
        != (
            previous.kind,
            previous.timeframe,
            float(previous.low),
            float(previous.high),
        )
    )


def _movement_signature_candidate(candidate: STSetupCandidate) -> tuple[Any, ...] | None:
    anchor = candidate.defended_anchor
    if candidate.family is STThesisFamily.UNRESOLVED or anchor is None:
        return None
    return (
        candidate.family.value,
        candidate.economic_mission.value,
        anchor.kind.value,
        anchor.identity,
        anchor.timeframe,
        float(anchor.low),
        float(anchor.high),
        candidate.target_identity,
    )


def _movement_signature_closed(previous: STClosedMovementRecord) -> tuple[Any, ...] | None:
    risk = previous.terminal_risk
    if previous.thesis_family is STThesisFamily.UNRESOLVED or risk is None:
        return None
    return (
        previous.thesis_family.value,
        previous.economic_mission.value,
        risk.kind,
        risk.identity,
        risk.timeframe,
        float(risk.low),
        float(risk.high),
        previous.initial_target_identity,
    )


def assess_st_setup_continuity(
    snapshot: "DecisionInputSnapshot",
    entry: Any,
    previous: STClosedMovementRecord | None,
) -> STSetupContinuityAssessment:
    """Compare a qualified candidate with the previous closed economic movement."""

    candidate = classify_qualified_st_setup_candidate(snapshot, entry)
    if previous is None:
        return STSetupContinuityAssessment(
            state=STSetupContinuityState.NO_PRIOR_MOVEMENT,
            candidate=candidate,
            new_information=False,
            new_risk_boundary=False,
            new_economic_move=False,
            protective_reversal_confirmed=True,
            reasons=("ST_REENTRY_NO_PRIOR_CLOSED_MOVEMENT",),
        )

    if candidate is None or candidate.family is STThesisFamily.UNRESOLVED:
        reasons = (
            "ST_REENTRY_SETUP_CONTINUITY_UNRESOLVED",
            *(() if candidate is None else candidate.reasons),
        )
        return STSetupContinuityAssessment(
            state=STSetupContinuityState.UNRESOLVED,
            candidate=candidate,
            new_information=False,
            new_risk_boundary=False,
            new_economic_move=False,
            protective_reversal_confirmed=False,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    post_exit_refs = tuple(
        ref for ref in candidate.source_refs if _confirmed_after(ref, previous.exit_as_of)
    )
    new_information = bool(post_exit_refs)
    new_risk = _new_risk_boundary(
        candidate.defended_anchor,
        previous.terminal_risk,
        has_new_information=new_information,
    )
    candidate_signature = _movement_signature_candidate(candidate)
    previous_signature = _movement_signature_closed(previous)
    new_move = bool(
        candidate_signature is not None
        and (previous_signature is None or candidate_signature != previous_signature)
    )

    post_exit_acceptance = any(
        ref.domain in {ContextDomain.SUPPORT_RESISTANCE, ContextDomain.MARKET_STRUCTURE}
        for ref in post_exit_refs
    )
    protective_reversal = (
        previous.exit_family is not STExitFamily.PROTECTIVE_EXIT
        or post_exit_acceptance
    )

    if new_information and new_risk and new_move and protective_reversal:
        return STSetupContinuityAssessment(
            state=STSetupContinuityState.NOVEL_SETUP,
            candidate=candidate,
            new_information=True,
            new_risk_boundary=True,
            new_economic_move=True,
            protective_reversal_confirmed=True,
            reasons=("ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED",),
        )

    reasons: list[str] = ["ST_REENTRY_SAME_ECONOMIC_MOVEMENT"]
    if not new_information:
        reasons.append("ST_REENTRY_NEW_INFORMATION_NOT_ESTABLISHED")
    if not new_risk:
        reasons.append("ST_REENTRY_NEW_RISK_BOUNDARY_NOT_ESTABLISHED")
    if not new_move:
        reasons.append("ST_REENTRY_NEW_ECONOMIC_MOVE_NOT_ESTABLISHED")
    if not protective_reversal:
        reasons.append("ST_REENTRY_PROTECTIVE_INVALIDATION_NOT_REVERSED")
    return STSetupContinuityAssessment(
        state=STSetupContinuityState.SAME_MOVEMENT,
        candidate=candidate,
        new_information=new_information,
        new_risk_boundary=new_risk,
        new_economic_move=new_move,
        protective_reversal_confirmed=protective_reversal,
        reasons=tuple(reasons),
    )


def _replace_entry(entry: Any, **changes: Any) -> Any:
    if is_dataclass(entry):
        return replace(entry, **changes)
    values = dict(vars(entry))
    values.update(changes)
    return SimpleNamespace(**values)


def apply_st_reentry_novelty_policy(
    entry: Any,
    assessment: STSetupContinuityAssessment,
) -> Any:
    """Gate only re-entry setup novelty; never reinterpret first-entry market gates."""

    reasons = tuple(
        dict.fromkeys((*getattr(entry, "reasons", ()), *assessment.reasons))
    )
    if assessment.state is STSetupContinuityState.NOVEL_SETUP:
        return _replace_entry(entry, reasons=reasons)
    if assessment.state is STSetupContinuityState.NO_PRIOR_MOVEMENT:
        return entry

    waiting = tuple(
        dict.fromkeys((*getattr(entry, "waiting_for", ()), "ST_REENTRY_NOVELTY_TO_ESTABLISH"))
    )
    return _replace_entry(
        entry,
        action=DecisionAction.WAIT,
        execution_event_consumed=False,
        reasons=reasons,
        blockers=(),
        waiting_for=waiting,
    )


def summarize_st_reentry_novelty(entries: Iterable[Any]) -> STReentryNoveltyMetrics:
    same = unresolved = released = executed = waiting = 0
    for entry in entries:
        reasons = set(getattr(entry, "reasons", ()))
        if "ST_REENTRY_SAME_ECONOMIC_MOVEMENT" in reasons:
            same += 1
        if "ST_REENTRY_SETUP_CONTINUITY_UNRESOLVED" in reasons:
            unresolved += 1
        if "ST_REENTRY_NOVEL_ECONOMIC_SETUP_CONFIRMED" in reasons:
            released += 1
            if getattr(entry, "action", None) is DecisionAction.BUY:
                executed += 1
            elif getattr(entry, "action", None) is DecisionAction.READY:
                waiting += 1
    return STReentryNoveltyMetrics(
        same_movement_blocks=same,
        unresolved_blocks=unresolved,
        novel_setups_released=released,
        novel_setups_executed=executed,
        novel_setups_waiting_execution=waiting,
    )


__all__ = [
    "STClosedMovementRecord",
    "STMovementRiskBoundary",
    "STReentryNoveltyMetrics",
    "STSetupCandidate",
    "STSetupContinuityAssessment",
    "STSetupContinuityState",
    "apply_st_reentry_novelty_policy",
    "assess_st_setup_continuity",
    "build_closed_st_movement_record",
    "classify_qualified_st_setup_candidate",
    "summarize_st_reentry_novelty",
]
