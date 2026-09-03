from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.lineage import LineageGroup, build_lineage_groups, unknown_lineage_refs
from financial_dashboard.context.participation_behavior_projection import (
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationTrend,
)
from financial_dashboard.context.pattern_behavior_projection import (
    PatternBehaviorPhase,
    _phase as _native_pattern_phase,
)

from .reaction import ReactionState, assess_reaction
from .structural import DecisionHorizon, StructuralAssessment, StructuralDirection, ThesisState


_ST_CONTROL_TIMEFRAMES = ("1h", "2h", "30m")
_ST_REACTION_TIMEFRAMES = ("1h", "30m")
_DIRECTIONAL_STATES = {
    StructuralDirection.LONG: {"BULLISH", "STATE_BULLISH"},
    StructuralDirection.SHORT: {"BEARISH", "STATE_BEARISH"},
}
_TRANSITION_STATES = {
    StructuralDirection.LONG: {"TRANSITION_UP", "STATE_TRANSITION_UP"},
    StructuralDirection.SHORT: {"TRANSITION_DOWN", "STATE_TRANSITION_DOWN"},
}


class IncumbentCondition(StrEnum):
    UNKNOWN = "UNKNOWN"
    DEFENDING = "DEFENDING"
    PROGRESSING = "PROGRESSING"
    WEAKENING = "WEAKENING"
    FAILING_TO_EXTEND = "FAILING_TO_EXTEND"
    LOSING_GROUND = "LOSING_GROUND"


class ChallengerCondition(StrEnum):
    UNKNOWN = "UNKNOWN"
    ABSENT = "ABSENT"
    EMERGING = "EMERGING"
    INITIATING = "INITIATING"
    GAINING_GROUND = "GAINING_GROUND"
    DEFENDING_GROUND = "DEFENDING_GROUND"
    FAILING = "FAILING"


class ShortTermControlState(StrEnum):
    UNKNOWN = "UNKNOWN"
    CONTROL_HELD = "CONTROL_HELD"
    CONTROL_WEAKENING = "CONTROL_WEAKENING"
    CONTROL_CONTESTED = "CONTROL_CONTESTED"
    TRANSFER_DEVELOPING = "TRANSFER_DEVELOPING"
    TRANSFER_ESTABLISHED = "TRANSFER_ESTABLISHED"
    TRANSFER_FAILED = "TRANSFER_FAILED"


class ControlEvidenceRole(StrEnum):
    INCUMBENT_PROGRESS = "INCUMBENT_PROGRESS"
    INCUMBENT_DEFENSE = "INCUMBENT_DEFENSE"
    INCUMBENT_FAILURE_TO_EXTEND = "INCUMBENT_FAILURE_TO_EXTEND"
    INCUMBENT_LOST_GROUND = "INCUMBENT_LOST_GROUND"
    CHALLENGER_EMERGENCE = "CHALLENGER_EMERGENCE"
    CHALLENGER_INITIATIVE = "CHALLENGER_INITIATIVE"
    CHALLENGER_ACCEPTANCE = "CHALLENGER_ACCEPTANCE"
    CHALLENGER_DEFENSE = "CHALLENGER_DEFENSE"
    CHALLENGER_FAILURE = "CHALLENGER_FAILURE"
    CONTROL_MIGRATION = "CONTROL_MIGRATION"
    TRANSFER_CONFIRMATION = "TRANSFER_CONFIRMATION"
    TRANSFER_INVALIDATION = "TRANSFER_INVALIDATION"


@dataclass(frozen=True, slots=True)
class ControlEvidence:
    """One typed economic role backed by already-frozen causal facts.

    Multiple refs may explain one role, but they never become a weight or vote count.
    """

    role: ControlEvidenceRole
    side: StructuralDirection
    reason: str
    source_refs: tuple[FactRef, ...]

    def __post_init__(self) -> None:
        if self.side is StructuralDirection.UNRESOLVED:
            raise ValueError("control evidence side must be directional")
        if not self.reason.strip():
            raise ValueError("control evidence reason must be non-empty")
        if not self.source_refs:
            raise ValueError("control evidence requires at least one causal source ref")


@dataclass(frozen=True, slots=True)
class ShortTermControlAssessment:
    """Action-free, stateless ST market-control synthesis."""

    symbol: str
    as_of: Any
    horizon: DecisionHorizon
    established_side: StructuralDirection
    challenger_side: StructuralDirection | None
    structure_state: str | None
    structure_transition_target: StructuralDirection | None
    incumbent_condition: IncumbentCondition
    challenger_condition: ChallengerCondition
    control_state: ShortTermControlState
    evidence: tuple[ControlEvidence, ...]
    source_refs: tuple[FactRef, ...]
    lineage_groups: tuple[LineageGroup, ...]
    unresolved_lineage_refs: tuple[FactRef, ...]
    data_quality: ContextDataQuality
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.horizon is not DecisionHorizon.SHORT_TERM:
            raise ValueError("short-term control assessment must use SHORT_TERM horizon")
        if not self.symbol.strip():
            raise ValueError("short-term control symbol must be non-empty")
        if self.as_of is None:
            raise ValueError("short-term control as_of must be known")
        if self.challenger_side is StructuralDirection.UNRESOLVED:
            raise ValueError("challenger side must be directional when present")
        if self.established_side is not StructuralDirection.UNRESOLVED:
            expected = _opposite(self.established_side)
            if self.challenger_side is not None and self.challenger_side is not expected:
                raise ValueError("challenger side must oppose established side")
        if any(ref.symbol != self.symbol for ref in self.source_refs):
            raise ValueError("control refs must match assessment symbol")
        if any(not ref.is_available_at(self.as_of) for ref in self.source_refs):
            raise ValueError("control assessment cannot contain future-unavailable refs")


@dataclass(frozen=True, slots=True)
class _RoleView:
    incumbent_progress: bool
    incumbent_defense: bool
    incumbent_failure: bool
    incumbent_lost_ground: bool
    challenger_emergence: bool
    challenger_initiative: bool
    challenger_acceptance: bool
    challenger_defense: bool
    challenger_failure: bool
    migration: bool
    transfer_confirmation: bool


def _direction_value(side: StructuralDirection) -> int:
    if side is StructuralDirection.LONG:
        return 1
    if side is StructuralDirection.SHORT:
        return -1
    return 0


def _side_from_value(value: int) -> StructuralDirection | None:
    if value > 0:
        return StructuralDirection.LONG
    if value < 0:
        return StructuralDirection.SHORT
    return None


def _opposite(side: StructuralDirection) -> StructuralDirection | None:
    if side is StructuralDirection.LONG:
        return StructuralDirection.SHORT
    if side is StructuralDirection.SHORT:
        return StructuralDirection.LONG
    return None


def _unique_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def _available_ref(
    ref: FactRef | None,
    *,
    as_of: Any,
    allow_data_limited: bool = False,
) -> FactRef | None:
    if ref is None or not ref.is_available_at(as_of):
        return None
    if ref.data_quality is ContextDataQuality.VALID:
        return ref
    if allow_data_limited and ref.data_quality is ContextDataQuality.DATA_LIMITED:
        return ref
    return None


def _effective_pattern_phase(row: Any | None) -> PatternBehaviorPhase | None:
    """Recover causal price-only Pattern phase without depending on Execution.

    This mirrors the production DATA_LIMITED native-state recovery semantics used by
    Timing/Execution, but keeps those layers out of Control and never promotes the
    frozen Pattern ref itself to VALID.
    """

    if row is None:
        return None
    phase = getattr(row, "phase", None)
    if phase is not None and not isinstance(phase, PatternBehaviorPhase):
        try:
            phase = PatternBehaviorPhase(str(getattr(phase, "value", phase)))
        except ValueError:
            phase = None
    ref = getattr(row, "ref", None)
    quality = getattr(ref, "data_quality", ContextDataQuality.UNAVAILABLE)
    if (
        phase is not None
        and phase is not PatternBehaviorPhase.UNAVAILABLE
        and quality is ContextDataQuality.VALID
    ):
        return phase
    native_state = str(getattr(row, "native_state", "") or "").strip()
    if not native_state:
        return phase
    return _native_pattern_phase(native_state, unavailable=False)


def _row(projection: Any, timeframe: str) -> Any | None:
    if projection is None:
        return None
    lookup = getattr(projection, "for_timeframe", None)
    if lookup is None:
        return None
    try:
        return lookup(timeframe)
    except (KeyError, AttributeError, TypeError):
        return None


def _add(
    evidence: list[ControlEvidence],
    role: ControlEvidenceRole,
    side: StructuralDirection,
    reason: str,
    refs: Iterable[FactRef],
) -> None:
    unique = _unique_refs(refs)
    if not unique:
        return
    item = ControlEvidence(role=role, side=side, reason=reason, source_refs=unique)
    if item not in evidence:
        evidence.append(item)


def _participation_evidence(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[ControlEvidence, ...]:
    projection = getattr(snapshot, "participation_behavior", None)
    if projection is None:
        return ()

    incumbent_value = _direction_value(incumbent)
    challenger_value = _direction_value(challenger)
    evidence: list[ControlEvidence] = []
    for timeframe in _ST_CONTROL_TIMEFRAMES:
        row = _row(projection, timeframe)
        if row is None:
            continue
        ref = _available_ref(getattr(row, "ref", None), as_of=snapshot.as_of)
        if ref is None:
            continue

        trend = getattr(row, "participation_trend", ParticipationTrend.UNAVAILABLE)
        effort = getattr(row, "effort_result", EffortResultBehavior.UNAVAILABLE)
        break_behavior = getattr(
            row,
            "break_participation",
            BreakParticipationBehavior.UNAVAILABLE,
        )
        participation_direction = int(getattr(row, "participation_direction", 0) or 0)
        evidence_direction = int(getattr(row, "evidence_direction", 0) or 0)
        break_direction = int(getattr(row, "break_direction", 0) or 0)

        incumbent_activity = (
            participation_direction == incumbent_value
            and trend
            in {
                ParticipationTrend.BUILDING,
                ParticipationTrend.CONFIRMED,
                ParticipationTrend.PROTECTED,
            }
        )
        incumbent_break_progress = (
            break_direction == incumbent_value
            and break_behavior
            in {BreakParticipationBehavior.SUPPORTED, BreakParticipationBehavior.PROTECTED}
        )
        if incumbent_activity or incumbent_break_progress:
            _add(
                evidence,
                ControlEvidenceRole.INCUMBENT_PROGRESS,
                incumbent,
                f"PARTICIPATION_INCUMBENT_PROGRESS:{timeframe}",
                (ref,),
            )

        incumbent_related = incumbent_value in {
            participation_direction,
            evidence_direction,
            break_direction,
        }
        if (
            break_direction == incumbent_value
            and break_behavior
            in {BreakParticipationBehavior.UNSUPPORTED, BreakParticipationBehavior.RECLAIMED}
        ) or (effort is EffortResultBehavior.WEAK_RESULT and incumbent_related):
            _add(
                evidence,
                ControlEvidenceRole.INCUMBENT_FAILURE_TO_EXTEND,
                incumbent,
                f"PARTICIPATION_INCUMBENT_FAILURE:{timeframe}:{break_behavior.value}:{effort.value}",
                (ref,),
            )
        if (
            break_direction == incumbent_value
            and break_behavior is BreakParticipationBehavior.RECLAIMED
        ):
            _add(
                evidence,
                ControlEvidenceRole.INCUMBENT_LOST_GROUND,
                incumbent,
                f"PARTICIPATION_INCUMBENT_BREAK_RECLAIMED:{timeframe}",
                (ref,),
            )

        challenger_activity = (
            participation_direction == challenger_value
            and trend
            in {
                ParticipationTrend.BUILDING,
                ParticipationTrend.CONFIRMED,
                ParticipationTrend.PROTECTED,
            }
        )
        challenger_break = break_direction == challenger_value
        challenger_evidence = evidence_direction == challenger_value and evidence_direction != 0
        if challenger_activity or challenger_evidence or (
            challenger_break and break_behavior is BreakParticipationBehavior.DEVELOPING
        ):
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_INITIATIVE,
                challenger,
                f"PARTICIPATION_CHALLENGER_INITIATIVE:{timeframe}",
                (ref,),
            )
        if challenger_break and break_behavior in {
            BreakParticipationBehavior.SUPPORTED,
            BreakParticipationBehavior.PROTECTED,
        }:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_ACCEPTANCE,
                challenger,
                f"PARTICIPATION_CHALLENGER_BREAK_{break_behavior.value}:{timeframe}",
                (ref,),
            )
        if challenger_break and break_behavior is BreakParticipationBehavior.PROTECTED:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_DEFENSE,
                challenger,
                f"PARTICIPATION_CHALLENGER_BREAK_PROTECTED:{timeframe}",
                (ref,),
            )

        challenger_related = challenger_value in {
            participation_direction,
            evidence_direction,
            break_direction,
        }
        if (
            challenger_break
            and break_behavior
            in {BreakParticipationBehavior.UNSUPPORTED, BreakParticipationBehavior.RECLAIMED}
        ) or (effort is EffortResultBehavior.WEAK_RESULT and challenger_related):
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_FAILURE,
                challenger,
                f"PARTICIPATION_CHALLENGER_FAILURE:{timeframe}:{break_behavior.value}:{effort.value}",
                (ref,),
            )
    return tuple(evidence)


def _pattern_evidence(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[ControlEvidence, ...]:
    row = _row(getattr(snapshot, "pattern_behavior", None), "30m")
    if row is None:
        return ()
    ref = _available_ref(
        getattr(row, "ref", None),
        as_of=snapshot.as_of,
        allow_data_limited=True,
    )
    if ref is None:
        return ()
    phase = _effective_pattern_phase(row)
    if phase is None or phase is PatternBehaviorPhase.UNAVAILABLE:
        return ()
    side = _side_from_value(int(getattr(row, "classic_direction", 0) or 0))
    if side is None:
        return ()

    evidence: list[ControlEvidence] = []
    forming = {PatternBehaviorPhase.FORMING, PatternBehaviorPhase.MATURE_COMPRESSION}
    initiating = {PatternBehaviorPhase.BREAK_ATTEMPT, PatternBehaviorPhase.BREAK_CONFIRMING}
    accepted = {PatternBehaviorPhase.BREAK_CONFIRMED, PatternBehaviorPhase.RETEST_HELD}
    failed = {
        PatternBehaviorPhase.BREAK_FAILED,
        PatternBehaviorPhase.WEAKENING,
        PatternBehaviorPhase.INVALIDATED,
    }

    if side is challenger:
        if phase in forming:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_EMERGENCE,
                challenger,
                f"PATTERN_CHALLENGER_EMERGENCE:30m:{phase.value}",
                (ref,),
            )
        if phase in initiating:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_INITIATIVE,
                challenger,
                f"PATTERN_CHALLENGER_INITIATIVE:30m:{phase.value}",
                (ref,),
            )
        if phase in accepted:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_ACCEPTANCE,
                challenger,
                f"PATTERN_CHALLENGER_ACCEPTANCE:30m:{phase.value}",
                (ref,),
            )
        if phase is PatternBehaviorPhase.RETEST_HELD:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_DEFENSE,
                challenger,
                "PATTERN_CHALLENGER_RETEST_HELD:30m",
                (ref,),
            )
        if phase in failed:
            _add(
                evidence,
                ControlEvidenceRole.CHALLENGER_FAILURE,
                challenger,
                f"PATTERN_CHALLENGER_FAILURE:30m:{phase.value}",
                (ref,),
            )
    elif side is incumbent:
        if phase in accepted:
            _add(
                evidence,
                ControlEvidenceRole.INCUMBENT_PROGRESS,
                incumbent,
                f"PATTERN_INCUMBENT_ACCEPTANCE:30m:{phase.value}",
                (ref,),
            )
        if phase is PatternBehaviorPhase.RETEST_HELD:
            _add(
                evidence,
                ControlEvidenceRole.INCUMBENT_DEFENSE,
                incumbent,
                "PATTERN_INCUMBENT_RETEST_HELD:30m",
                (ref,),
            )
        if phase in failed:
            _add(
                evidence,
                ControlEvidenceRole.INCUMBENT_FAILURE_TO_EXTEND,
                incumbent,
                f"PATTERN_INCUMBENT_FAILURE:30m:{phase.value}",
                (ref,),
            )
    return tuple(evidence)


def _reaction_evidence(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[ControlEvidence, ...]:
    incumbent_reaction = assess_reaction(
        incumbent,
        order_blocks=getattr(snapshot, "order_block_behavior", None),
        fvg_engulfing=getattr(snapshot, "fvg_engulfing_lifecycle", None),
        timeframes=_ST_REACTION_TIMEFRAMES,
    )
    challenger_reaction = assess_reaction(
        challenger,
        order_blocks=getattr(snapshot, "order_block_behavior", None),
        fvg_engulfing=getattr(snapshot, "fvg_engulfing_lifecycle", None),
        timeframes=_ST_REACTION_TIMEFRAMES,
    )
    evidence: list[ControlEvidence] = []

    if incumbent_reaction.state is ReactionState.CONFIRMED:
        _add(
            evidence,
            ControlEvidenceRole.INCUMBENT_DEFENSE,
            incumbent,
            "REACTION_INCUMBENT_CONFIRMED",
            incumbent_reaction.source_refs,
        )
    elif incumbent_reaction.state is ReactionState.FAILED:
        _add(
            evidence,
            ControlEvidenceRole.INCUMBENT_LOST_GROUND,
            incumbent,
            "REACTION_INCUMBENT_FAILED",
            incumbent_reaction.source_refs,
        )

    if challenger_reaction.state is ReactionState.DEVELOPING:
        _add(
            evidence,
            ControlEvidenceRole.CHALLENGER_EMERGENCE,
            challenger,
            "REACTION_CHALLENGER_DEVELOPING",
            challenger_reaction.source_refs,
        )
    elif challenger_reaction.state is ReactionState.CONFIRMED:
        _add(
            evidence,
            ControlEvidenceRole.CHALLENGER_ACCEPTANCE,
            challenger,
            "REACTION_CHALLENGER_CONFIRMED",
            challenger_reaction.source_refs,
        )
        _add(
            evidence,
            ControlEvidenceRole.CHALLENGER_DEFENSE,
            challenger,
            "REACTION_CHALLENGER_DEFENSE_CONFIRMED",
            challenger_reaction.source_refs,
        )
    elif challenger_reaction.state is ReactionState.FAILED:
        _add(
            evidence,
            ControlEvidenceRole.CHALLENGER_FAILURE,
            challenger,
            "REACTION_CHALLENGER_FAILED",
            challenger_reaction.source_refs,
        )
    return tuple(evidence)


def _support_resistance_evidence(
    snapshot: Any,
    *,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[ControlEvidence, ...]:
    projection = getattr(snapshot, "support_resistance", None)
    if projection is None:
        return ()
    incumbent_value = _direction_value(incumbent)
    challenger_value = _direction_value(challenger)
    evidence: list[ControlEvidence] = []

    for timeframe in _ST_CONTROL_TIMEFRAMES:
        row = _row(projection, timeframe)
        if row is None:
            continue
        ref = _available_ref(getattr(row, "ref", None), as_of=snapshot.as_of)
        if ref is None:
            continue
        break_direction = int(getattr(row, "break_direction", 0) or 0)
        if break_direction == 0 or getattr(row, "break_confirmed_index", None) is None:
            continue
        location = str(getattr(row, "price_location", "") or "").strip().upper()
        accepted = (
            (break_direction > 0 and location == "ABOVE_RANGE")
            or (break_direction < 0 and location == "BELOW_RANGE")
        )
        reclaimed = location in {"INSIDE_RANGE", "UPPER_ZONE", "LOWER_ZONE"}

        if break_direction == incumbent_value:
            if accepted:
                _add(
                    evidence,
                    ControlEvidenceRole.INCUMBENT_PROGRESS,
                    incumbent,
                    f"SR_INCUMBENT_BREAK_ACCEPTED:{timeframe}:{location}",
                    (ref,),
                )
            elif reclaimed:
                _add(
                    evidence,
                    ControlEvidenceRole.INCUMBENT_LOST_GROUND,
                    incumbent,
                    f"SR_INCUMBENT_BREAK_RECLAIMED:{timeframe}:{location}",
                    (ref,),
                )
        elif break_direction == challenger_value:
            if accepted:
                _add(
                    evidence,
                    ControlEvidenceRole.CHALLENGER_ACCEPTANCE,
                    challenger,
                    f"SR_CHALLENGER_BREAK_ACCEPTED:{timeframe}:{location}",
                    (ref,),
                )
            elif reclaimed:
                _add(
                    evidence,
                    ControlEvidenceRole.CHALLENGER_FAILURE,
                    challenger,
                    f"SR_CHALLENGER_BREAK_RECLAIMED:{timeframe}:{location}",
                    (ref,),
                )
    return tuple(evidence)


def _latest_structural_refs_for_side(
    row: Any,
    *,
    side: StructuralDirection,
    as_of: Any,
) -> tuple[FactRef, ...]:
    value = _direction_value(side)
    candidates: list[FactRef] = []
    for event in getattr(row, "events", ()):
        if int(getattr(event, "direction", 0) or 0) != value:
            continue
        if str(getattr(event, "confirmation_status", "")).strip().upper() != "CONFIRMED":
            continue
        if str(getattr(event, "validity", "")).strip().upper() != "VALID":
            continue
        ref = _available_ref(getattr(event, "ref", None), as_of=as_of)
        if ref is not None:
            candidates.append(ref)
    confirmed = [ref for ref in candidates if ref.confirmed_at is not None]
    if not confirmed:
        return _unique_refs(candidates)
    latest_time = max(ref.confirmed_at for ref in confirmed)
    return _unique_refs(ref for ref in confirmed if ref.confirmed_at == latest_time)


def _latest_authority_bos(
    row: Any,
    *,
    side: StructuralDirection,
    as_of: Any,
) -> tuple[tuple[Any, FactRef], ...]:
    """Return only the newest causal external BOS events for one authority side."""

    side_value = _direction_value(side)
    candidates: list[tuple[Any, FactRef]] = []
    for event in getattr(row, "events", ()):
        if str(getattr(event, "scope", "")).strip().upper() != "EXTERNAL":
            continue
        if str(getattr(event, "event_type", "")).strip().upper() != "EVENT_BOS":
            continue
        if int(getattr(event, "direction", 0) or 0) != side_value:
            continue
        if str(getattr(event, "confirmation_status", "")).strip().upper() != "CONFIRMED":
            continue
        if str(getattr(event, "validity", "")).strip().upper() != "VALID":
            continue
        ref = _available_ref(getattr(event, "ref", None), as_of=as_of)
        if ref is not None and ref.confirmed_at is not None:
            candidates.append((event, ref))
    if not candidates:
        return ()
    latest_time = max(ref.confirmed_at for _, ref in candidates)
    return tuple(
        sorted(
            ((event, ref) for event, ref in candidates if ref.confirmed_at == latest_time),
            key=lambda item: item[1].deterministic_key,
        )
    )


def _structure_evidence(
    snapshot: Any,
    *,
    structural: StructuralAssessment,
    incumbent: StructuralDirection,
    challenger: StructuralDirection,
) -> tuple[ControlEvidence, ...]:
    projection = getattr(snapshot, "structure", None)
    if projection is None:
        return ()
    evidence: list[ControlEvidence] = []

    for timeframe in ("2h", "30m"):
        row = _row(projection, timeframe)
        if (
            row is None
            or getattr(row, "data_quality", ContextDataQuality.UNAVAILABLE)
            is not ContextDataQuality.VALID
        ):
            continue
        for scope_name in ("external", "internal"):
            scope = getattr(row, scope_name, None)
            if scope is None:
                continue
            state = str(getattr(scope, "state", "") or "").strip().upper()
            direction = int(getattr(scope, "direction", 0) or 0)
            target_states = _DIRECTIONAL_STATES[challenger] | _TRANSITION_STATES[challenger]
            if direction != _direction_value(challenger) and state not in target_states:
                continue
            refs = _latest_structural_refs_for_side(
                row,
                side=challenger,
                as_of=snapshot.as_of,
            )
            _add(
                evidence,
                ControlEvidenceRole.CONTROL_MIGRATION,
                challenger,
                f"STRUCTURE_MIGRATION:{timeframe}:{scope_name.upper()}:{state or 'UNKNOWN'}",
                refs,
            )

    authority = _row(projection, structural.authority_timeframe)
    if authority is not None and structural.thesis_state is ThesisState.INTACT:
        latest = _latest_authority_bos(
            authority,
            side=incumbent,
            as_of=snapshot.as_of,
        )
        confirmation_refs = tuple(
            ref
            for event, ref in latest
            if str(getattr(event, "bos_maturity", "")).strip().upper()
            == "TRANSITION_CONFIRMATION"
        )
        if confirmation_refs:
            _add(
                evidence,
                ControlEvidenceRole.TRANSFER_CONFIRMATION,
                incumbent,
                f"STRUCTURE_LATEST_TARGET_SIDE_TRANSITION_CONFIRMATION:{structural.authority_timeframe}",
                confirmation_refs,
            )
    return tuple(evidence)


def _role_view(evidence: tuple[ControlEvidence, ...]) -> _RoleView:
    roles = {item.role for item in evidence}
    return _RoleView(
        incumbent_progress=ControlEvidenceRole.INCUMBENT_PROGRESS in roles,
        incumbent_defense=ControlEvidenceRole.INCUMBENT_DEFENSE in roles,
        incumbent_failure=ControlEvidenceRole.INCUMBENT_FAILURE_TO_EXTEND in roles,
        incumbent_lost_ground=ControlEvidenceRole.INCUMBENT_LOST_GROUND in roles,
        challenger_emergence=ControlEvidenceRole.CHALLENGER_EMERGENCE in roles,
        challenger_initiative=ControlEvidenceRole.CHALLENGER_INITIATIVE in roles,
        challenger_acceptance=ControlEvidenceRole.CHALLENGER_ACCEPTANCE in roles,
        challenger_defense=ControlEvidenceRole.CHALLENGER_DEFENSE in roles,
        challenger_failure=ControlEvidenceRole.CHALLENGER_FAILURE in roles,
        migration=ControlEvidenceRole.CONTROL_MIGRATION in roles,
        transfer_confirmation=ControlEvidenceRole.TRANSFER_CONFIRMATION in roles,
    )


def _has_non_structural_role(
    evidence: tuple[ControlEvidence, ...],
    role: ControlEvidenceRole,
) -> bool:
    return any(
        item.role is role
        and any(ref.causal_family.value != "STRUCTURAL_LEVEL" for ref in item.source_refs)
        for item in evidence
    )


def _incumbent_condition(
    structural: StructuralAssessment,
    roles: _RoleView,
) -> IncumbentCondition:
    if structural.direction is StructuralDirection.UNRESOLVED:
        return IncumbentCondition.UNKNOWN
    if roles.incumbent_lost_ground:
        return IncumbentCondition.LOSING_GROUND
    if roles.incumbent_failure:
        return IncumbentCondition.FAILING_TO_EXTEND
    if roles.incumbent_progress:
        return IncumbentCondition.PROGRESSING
    if roles.incumbent_defense:
        return IncumbentCondition.DEFENDING
    if structural.thesis_state is ThesisState.TRANSITIONING:
        return IncumbentCondition.WEAKENING
    return IncumbentCondition.DEFENDING


def _challenger_condition(
    roles: _RoleView,
    *,
    evidence_present: bool,
) -> ChallengerCondition:
    if (
        roles.challenger_failure
        and not roles.challenger_acceptance
        and not roles.challenger_defense
    ):
        return ChallengerCondition.FAILING
    if roles.challenger_defense:
        return ChallengerCondition.DEFENDING_GROUND
    if roles.challenger_acceptance:
        return ChallengerCondition.GAINING_GROUND
    if roles.challenger_initiative or roles.migration:
        return ChallengerCondition.INITIATING
    if roles.challenger_emergence:
        return ChallengerCondition.EMERGING
    if evidence_present:
        return ChallengerCondition.ABSENT
    return ChallengerCondition.UNKNOWN


def _control_state(
    structural: StructuralAssessment,
    roles: _RoleView,
    *,
    challenger_acceptance_non_structural: bool,
) -> tuple[ShortTermControlState, str]:
    if structural.direction is StructuralDirection.UNRESOLVED:
        return ShortTermControlState.UNKNOWN, "STRUCTURE_DIRECTION_UNRESOLVED"

    if roles.transfer_confirmation and structural.thesis_state is ThesisState.INTACT:
        return (
            ShortTermControlState.TRANSFER_ESTABLISHED,
            "LATEST_STRUCTURE_BOS_ESTABLISHES_TRANSFER",
        )

    active_transition = (
        structural.thesis_state is ThesisState.TRANSITIONING
        and structural.transition_target is not None
    )
    if active_transition:
        if (
            roles.challenger_failure
            and (roles.incumbent_progress or roles.incumbent_defense)
            and not roles.challenger_acceptance
            and not roles.challenger_defense
        ):
            return (
                ShortTermControlState.TRANSFER_FAILED,
                "CHALLENGER_FAILED_WHILE_INCUMBENT_REASSERTED",
            )
        if (
            roles.challenger_acceptance
            and challenger_acceptance_non_structural
            and roles.migration
            and not roles.challenger_failure
        ):
            return (
                ShortTermControlState.TRANSFER_DEVELOPING,
                "NON_STRUCTURAL_CHALLENGER_ACCEPTANCE_WITH_LOWER_TF_MIGRATION",
            )
        if roles.challenger_acceptance:
            return (
                ShortTermControlState.CONTROL_CONTESTED,
                "CHALLENGER_ACCEPTANCE_NOT_YET_INDEPENDENTLY_MIGRATED",
            )
        if roles.challenger_initiative or roles.challenger_emergence or roles.migration:
            return (
                ShortTermControlState.CONTROL_CONTESTED,
                "TRANSITION_WITH_ACTIVE_CHALLENGER",
            )
        if roles.incumbent_failure or roles.incumbent_lost_ground:
            return (
                ShortTermControlState.CONTROL_WEAKENING,
                "INCUMBENT_CONTINUATION_DETERIORATING",
            )
        return (
            ShortTermControlState.CONTROL_WEAKENING,
            "STRUCTURE_TRANSITION_ANCHOR_ONLY",
        )

    if roles.incumbent_lost_ground or roles.incumbent_failure:
        if roles.challenger_acceptance or roles.challenger_initiative or roles.migration:
            return (
                ShortTermControlState.CONTROL_CONTESTED,
                "INTACT_STRUCTURE_WITH_COUNTER_CONTROL_PRESSURE",
            )
        return (
            ShortTermControlState.CONTROL_WEAKENING,
            "INTACT_STRUCTURE_WITH_INCUMBENT_DETERIORATION",
        )
    return ShortTermControlState.CONTROL_HELD, "INTACT_STRUCTURE_CONTROL_HELD"


def _assessment_quality(
    structural: StructuralAssessment,
    refs: tuple[FactRef, ...],
) -> ContextDataQuality:
    if structural.data_quality is not ContextDataQuality.VALID:
        return structural.data_quality
    if any(ref.data_quality is ContextDataQuality.DATA_LIMITED for ref in refs):
        return ContextDataQuality.DATA_LIMITED
    return ContextDataQuality.VALID


def assess_short_term_control(
    snapshot: Any,
    *,
    structural: StructuralAssessment,
) -> ShortTermControlAssessment:
    """Build one stateless, action-free ST control assessment from frozen facts.

    The caller supplies canonical ST Structure. Timing, Opportunity, Execution,
    Context, Permission and position state are deliberately outside this function.
    """

    if structural.horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("short-term control requires a SHORT_TERM StructuralAssessment")
    if getattr(snapshot, "as_of", None) is None:
        raise ValueError("short-term control snapshot as_of must be known")
    symbol = str(getattr(snapshot, "symbol", "") or "").strip()
    if not symbol:
        raise ValueError("short-term control snapshot symbol must be non-empty")

    incumbent = structural.direction
    challenger = (
        structural.transition_target
        if structural.transition_target is not None
        else _opposite(incumbent)
    )
    if incumbent is StructuralDirection.UNRESOLVED or challenger is None:
        refs = _unique_refs(structural.source_refs)
        return ShortTermControlAssessment(
            symbol=symbol,
            as_of=snapshot.as_of,
            horizon=DecisionHorizon.SHORT_TERM,
            established_side=incumbent,
            challenger_side=None,
            structure_state=structural.native_state,
            structure_transition_target=structural.transition_target,
            incumbent_condition=IncumbentCondition.UNKNOWN,
            challenger_condition=ChallengerCondition.UNKNOWN,
            control_state=ShortTermControlState.UNKNOWN,
            evidence=(),
            source_refs=refs,
            lineage_groups=build_lineage_groups(refs),
            unresolved_lineage_refs=unknown_lineage_refs(refs),
            data_quality=structural.data_quality,
            reasons=("ST_CONTROL_STRUCTURE_UNRESOLVED",),
        )

    evidence = tuple(
        sorted(
            (
                *_participation_evidence(
                    snapshot,
                    incumbent=incumbent,
                    challenger=challenger,
                ),
                *_pattern_evidence(
                    snapshot,
                    incumbent=incumbent,
                    challenger=challenger,
                ),
                *_reaction_evidence(
                    snapshot,
                    incumbent=incumbent,
                    challenger=challenger,
                ),
                *_support_resistance_evidence(
                    snapshot,
                    incumbent=incumbent,
                    challenger=challenger,
                ),
                *_structure_evidence(
                    snapshot,
                    structural=structural,
                    incumbent=incumbent,
                    challenger=challenger,
                ),
            ),
            key=lambda item: (
                item.role.value,
                item.side.value,
                item.reason,
                tuple(ref.deterministic_key for ref in item.source_refs),
            ),
        )
    )
    roles = _role_view(evidence)
    non_structural_evidence_present = any(
        any(ref.causal_family.value != "STRUCTURAL_LEVEL" for ref in item.source_refs)
        for item in evidence
    )
    incumbent_condition = _incumbent_condition(structural, roles)
    challenger_condition = _challenger_condition(
        roles,
        evidence_present=non_structural_evidence_present,
    )
    control_state, state_reason = _control_state(
        structural,
        roles,
        challenger_acceptance_non_structural=_has_non_structural_role(
            evidence,
            ControlEvidenceRole.CHALLENGER_ACCEPTANCE,
        ),
    )

    refs = _unique_refs(
        (
            *structural.source_refs,
            *(ref for item in evidence for ref in item.source_refs),
        )
    )
    lineage_groups = build_lineage_groups(refs)
    unresolved = unknown_lineage_refs(refs)
    reasons = tuple(
        dict.fromkeys(
            (
                state_reason,
                f"INCUMBENT:{incumbent_condition.value}",
                f"CHALLENGER:{challenger_condition.value}",
                *(f"ROLE:{item.role.value}:{item.reason}" for item in evidence),
                (
                    "UNKNOWN_LINEAGE_NOT_PROMOTED_TO_INDEPENDENCE"
                    if unresolved
                    else "LINEAGE_EXPLICIT_OR_NOT_REQUIRED"
                ),
            )
        )
    )
    return ShortTermControlAssessment(
        symbol=symbol,
        as_of=snapshot.as_of,
        horizon=DecisionHorizon.SHORT_TERM,
        established_side=incumbent,
        challenger_side=challenger,
        structure_state=structural.native_state,
        structure_transition_target=structural.transition_target,
        incumbent_condition=incumbent_condition,
        challenger_condition=challenger_condition,
        control_state=control_state,
        evidence=evidence,
        source_refs=refs,
        lineage_groups=lineage_groups,
        unresolved_lineage_refs=unresolved,
        data_quality=_assessment_quality(structural, refs),
        reasons=reasons,
    )


__all__ = [
    "ChallengerCondition",
    "ControlEvidence",
    "ControlEvidenceRole",
    "IncumbentCondition",
    "ShortTermControlAssessment",
    "ShortTermControlState",
    "assess_short_term_control",
]
