from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING, Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef

from .composer import DecisionAction
from .scenario import ScenarioKind, ScenarioStage
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision.entry import EntryDecision
    from financial_dashboard.decision.lifecycle_replay import CanonicalLifecycleReplayResult
    from financial_dashboard.decision_input import DecisionInputSnapshot


class STThesisFamily(StrEnum):
    PULLBACK_CONTINUATION = "PULLBACK_CONTINUATION"
    BREAKOUT_ACCEPTANCE = "BREAKOUT_ACCEPTANCE"
    FAILED_SELL_RECLAIM = "FAILED_SELL_RECLAIM"
    UNRESOLVED = "UNRESOLVED"


class STEconomicMission(StrEnum):
    CONTINUE_AFTER_BUYER_REGAIN = "CONTINUE_AFTER_BUYER_REGAIN"
    EXPAND_FROM_ACCEPTED_HIGHER_AREA = "EXPAND_FROM_ACCEPTED_HIGHER_AREA"
    CAPTURE_FAILED_SELL_RECLAIM = "CAPTURE_FAILED_SELL_RECLAIM"
    UNRESOLVED = "UNRESOLVED"


class STDefendedAnchorKind(StrEnum):
    REACTION_ZONE = "REACTION_ZONE"
    BREAKOUT_ROLE_SUPPORT = "BREAKOUT_ROLE_SUPPORT"
    FAILED_SELL_RECLAIM_LEVEL = "FAILED_SELL_RECLAIM_LEVEL"


@dataclass(frozen=True, slots=True)
class STDefendedAnchor:
    kind: STDefendedAnchorKind
    identity: str
    timeframe: str
    low: float
    high: float
    source_refs: tuple[FactRef, ...]

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("ST defended anchor identity must be non-empty")
        if not self.timeframe.strip():
            raise ValueError("ST defended anchor timeframe must be non-empty")
        if not isfinite(float(self.low)) or not isfinite(float(self.high)):
            raise ValueError("ST defended anchor bounds must be finite")
        if float(self.low) > float(self.high):
            raise ValueError("ST defended anchor low cannot exceed high")
        canonical = _unique_refs(self.source_refs)
        if canonical != self.source_refs:
            raise ValueError("ST defended anchor refs must be sorted and unique")


@dataclass(frozen=True, slots=True)
class STThesisIdentityShadow:
    symbol: str
    entry_as_of: Any
    entry_price: float
    family: STThesisFamily
    economic_mission: STEconomicMission
    initial_defended_anchor: STDefendedAnchor | None
    initial_target_identity: str | None
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("ST thesis shadow symbol must be non-empty")
        if self.entry_as_of is None:
            raise ValueError("ST thesis shadow entry_as_of must be known")
        if not isfinite(float(self.entry_price)) or float(self.entry_price) <= 0.0:
            raise ValueError("ST thesis shadow entry price must be finite and positive")
        if not self.reasons:
            raise ValueError("ST thesis shadow requires at least one reason")
        if _unique_refs(self.source_refs) != self.source_refs:
            raise ValueError("ST thesis shadow refs must be sorted and unique")

        if self.family is STThesisFamily.UNRESOLVED:
            if self.economic_mission is not STEconomicMission.UNRESOLVED:
                raise ValueError("unresolved ST thesis cannot carry a resolved mission")
            if self.initial_defended_anchor is not None:
                raise ValueError("unresolved ST thesis cannot freeze a defended anchor")
            return

        if self.economic_mission is STEconomicMission.UNRESOLVED:
            raise ValueError("resolved ST thesis requires an economic mission")
        if self.initial_defended_anchor is None:
            raise ValueError("resolved ST thesis requires an initial defended anchor")
        if self.initial_target_identity is None or not self.initial_target_identity.strip():
            raise ValueError("resolved ST thesis requires initial target context")


@dataclass(frozen=True, slots=True)
class STThesisIdentityCoverage:
    executed_st_entries: int
    resolved_entries: int
    unresolved_entries: int
    family_counts: tuple[tuple[STThesisFamily, int], ...]

    def __post_init__(self) -> None:
        if min(self.executed_st_entries, self.resolved_entries, self.unresolved_entries) < 0:
            raise ValueError("ST thesis coverage counts cannot be negative")
        if self.resolved_entries + self.unresolved_entries != self.executed_st_entries:
            raise ValueError("ST thesis coverage counts must partition executed ST entries")
        if sum(count for _, count in self.family_counts) != self.executed_st_entries:
            raise ValueError("ST thesis family counts must cover every executed ST entry")


@dataclass(frozen=True, slots=True)
class STThesisIdentityShadowReport:
    entries: tuple[STThesisIdentityShadow, ...]
    coverage: STThesisIdentityCoverage


@dataclass(frozen=True, slots=True)
class _Candidate:
    family: STThesisFamily
    mission: STEconomicMission
    anchor: STDefendedAnchor
    reason: str


def _unique_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def _causal_valid_ref(ref: FactRef | None, as_of: Any) -> bool:
    if ref is None or ref.data_quality is not ContextDataQuality.VALID:
        return False
    try:
        return ref.is_available_at(as_of)
    except TypeError:
        return False


def _one_timeframe_fact(projection: Any | None, timeframe: str) -> Any | None:
    normalized = timeframe.strip().lower()
    if projection is None:
        return None
    for item in getattr(projection, "timeframe_facts", ()):
        if str(getattr(item, "timeframe", "")).strip().lower() == normalized:
            return item
    return None


def _executed_st_scenario(entry: "EntryDecision") -> Any | None:
    if entry.action is not DecisionAction.BUY:
        return None
    if entry.selected_horizon is not DecisionHorizon.SHORT_TERM:
        return None
    if entry.scenario_stage is not ScenarioStage.QUALIFIED:
        return None
    if not bool(entry.execution_event_consumed):
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


def _sr_candidate(snapshot: "DecisionInputSnapshot") -> _Candidate | None:
    row = _one_timeframe_fact(getattr(snapshot, "support_resistance", None), "1h")
    ref = None if row is None else getattr(row, "ref", None)
    if row is None or not _causal_valid_ref(ref, snapshot.as_of):
        return None

    state = str(getattr(row, "state", "") or "").strip().upper()
    direction = int(getattr(row, "break_direction", 0) or 0)
    boundary = getattr(row, "break_boundary", None)
    current_price = float(snapshot.current_price)

    if state == "RANGE_BREAK_CONFIRMED" and direction == 1:
        support_low = getattr(row, "role_reversal_support_low", None)
        support_high = getattr(row, "role_reversal_support_high", None)
        if (
            boundary is not None
            and support_low is not None
            and support_high is not None
            and current_price > float(boundary)
            and float(support_low) <= float(support_high)
        ):
            identity = getattr(row, "range_identity", None)
            return _Candidate(
                family=STThesisFamily.BREAKOUT_ACCEPTANCE,
                mission=STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
                anchor=STDefendedAnchor(
                    kind=STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT,
                    identity=f"SR_BREAKOUT:{identity if identity is not None else 'UNKNOWN'}",
                    timeframe="1h",
                    low=float(support_low),
                    high=float(support_high),
                    source_refs=(ref,),
                ),
                reason="ST_THESIS_BREAKOUT_ACCEPTANCE_CAUSAL",
            )

    if state == "RANGE_BREAK_FAILED" and direction == -1 and boundary is not None:
        candidate_index = getattr(row, "break_candidate_index", None)
        if candidate_index is not None and current_price >= float(boundary):
            identity = getattr(row, "range_identity", None)
            return _Candidate(
                family=STThesisFamily.FAILED_SELL_RECLAIM,
                mission=STEconomicMission.CAPTURE_FAILED_SELL_RECLAIM,
                anchor=STDefendedAnchor(
                    kind=STDefendedAnchorKind.FAILED_SELL_RECLAIM_LEVEL,
                    identity=f"SR_FAILED_SELL:{identity if identity is not None else 'UNKNOWN'}",
                    timeframe="1h",
                    low=float(boundary),
                    high=float(boundary),
                    source_refs=(ref,),
                ),
                reason="ST_THESIS_FAILED_SELL_RECLAIM_CAUSAL",
            )

    return None


def _reaction_anchor_candidates(snapshot: "DecisionInputSnapshot") -> tuple[STDefendedAnchor, ...]:
    anchors: list[STDefendedAnchor] = []
    as_of = snapshot.as_of

    order_blocks = getattr(snapshot, "order_block_behavior", None)
    for item in (() if order_blocks is None else getattr(order_blocks, "observations", ())):
        if str(getattr(item, "timeframe", "")).strip().lower() != "1h":
            continue
        if not bool(getattr(item, "bullish", False)):
            continue
        ref = getattr(item, "ref", None)
        if not _causal_valid_ref(ref, as_of):
            continue
        state = str(getattr(item, "state", "") or "").strip().upper()
        interaction = str(getattr(item, "interaction", "") or "").strip().upper()
        if state != "REACTION_CONFIRMED" and interaction != "REACTION_CONFIRMED":
            continue
        anchors.append(
            STDefendedAnchor(
                kind=STDefendedAnchorKind.REACTION_ZONE,
                identity=f"OB:{getattr(item, 'identity', 'UNKNOWN')}",
                timeframe="1h",
                low=float(getattr(item, "bottom")),
                high=float(getattr(item, "top")),
                source_refs=(ref,),
            )
        )

    lifecycle = getattr(snapshot, "fvg_engulfing_lifecycle", None)
    for item in (() if lifecycle is None else getattr(lifecycle, "fvg", ())):
        ref = getattr(item, "ref", None)
        if str(getattr(ref, "timeframe", "")).strip().lower() != "1h":
            continue
        if int(getattr(item, "direction", 0) or 0) != 1:
            continue
        if not _causal_valid_ref(ref, as_of):
            continue
        if not bool(getattr(item, "reaction_confirmed", False)):
            continue
        if any(
            bool(getattr(item, name, False))
            for name in ("failed_reaction", "full_fill", "invalid")
        ):
            continue
        anchors.append(
            STDefendedAnchor(
                kind=STDefendedAnchorKind.REACTION_ZONE,
                identity=f"FVG:{getattr(item, 'identity', 'UNKNOWN')}",
                timeframe="1h",
                low=float(getattr(item, "lower_boundary")),
                high=float(getattr(item, "upper_boundary")),
                source_refs=(ref,),
            )
        )

    return tuple(anchors)


def _coherent_reaction_anchor(snapshot: "DecisionInputSnapshot") -> STDefendedAnchor | None:
    anchors = _reaction_anchor_candidates(snapshot)
    if not anchors:
        return None
    if len(anchors) == 1:
        return anchors[0]

    low = max(anchor.low for anchor in anchors)
    high = min(anchor.high for anchor in anchors)
    if low > high:
        return None

    return STDefendedAnchor(
        kind=STDefendedAnchorKind.REACTION_ZONE,
        identity="REACTION_OVERLAP:" + "|".join(sorted(anchor.identity for anchor in anchors)),
        timeframe="1h",
        low=low,
        high=high,
        source_refs=_unique_refs(ref for anchor in anchors for ref in anchor.source_refs),
    )


def _pullback_candidate(
    snapshot: "DecisionInputSnapshot",
    scenario: Any,
) -> _Candidate | None:
    # Generic ScenarioKind is not the thesis identity. Here it is used only as causal
    # evidence that the ST long belongs to an already-long larger structural context;
    # a concrete 1h buyer-regain zone is still required to identify the pullback thesis.
    if getattr(scenario, "kind", None) is not ScenarioKind.CONTINUATION:
        return None
    anchor = _coherent_reaction_anchor(snapshot)
    if anchor is None:
        return None
    return _Candidate(
        family=STThesisFamily.PULLBACK_CONTINUATION,
        mission=STEconomicMission.CONTINUE_AFTER_BUYER_REGAIN,
        anchor=anchor,
        reason="ST_THESIS_PULLBACK_CONTINUATION_CAUSAL",
    )


def classify_executed_st_thesis(
    snapshot: "DecisionInputSnapshot",
    entry: "EntryDecision",
) -> STThesisIdentityShadow | None:
    """Classify one actually executed ST BUY without affecting trading behavior.

    Only evidence available on the entry snapshot is read. Ambiguity is represented
    explicitly as UNRESOLVED; this function never reinterprets eligibility, arbitration,
    execution, or the canonical action.
    """

    scenario = _executed_st_scenario(entry)
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
    candidate_refs = _unique_refs(
        ref for candidate in candidates for ref in candidate.anchor.source_refs
    )

    if target_identity is None:
        return STThesisIdentityShadow(
            symbol=str(snapshot.symbol),
            entry_as_of=snapshot.as_of,
            entry_price=float(snapshot.current_price),
            family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            initial_defended_anchor=None,
            initial_target_identity=None,
            reasons=("ST_THESIS_INITIAL_TARGET_CONTEXT_UNRESOLVED",),
            source_refs=candidate_refs,
        )

    families = {candidate.family for candidate in candidates}
    if len(families) != 1 or len(candidates) != 1:
        if not candidates:
            reasons = ("ST_THESIS_CAUSAL_EVIDENCE_INSUFFICIENT",)
        else:
            reasons = (
                "ST_THESIS_AMBIGUOUS_FAMILIES:"
                + ",".join(sorted(family.value for family in families)),
            )
        return STThesisIdentityShadow(
            symbol=str(snapshot.symbol),
            entry_as_of=snapshot.as_of,
            entry_price=float(snapshot.current_price),
            family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            initial_defended_anchor=None,
            initial_target_identity=target_identity,
            reasons=reasons,
            source_refs=candidate_refs,
        )

    candidate = candidates[0]
    return STThesisIdentityShadow(
        symbol=str(snapshot.symbol),
        entry_as_of=snapshot.as_of,
        entry_price=float(snapshot.current_price),
        family=candidate.family,
        economic_mission=candidate.mission,
        initial_defended_anchor=candidate.anchor,
        initial_target_identity=target_identity,
        reasons=(candidate.reason,),
        source_refs=candidate.anchor.source_refs,
    )


def audit_st_thesis_identity_shadow(
    replay: "CanonicalLifecycleReplayResult",
) -> STThesisIdentityShadowReport:
    """Produce Step-1 classification coverage from an already-computed canonical replay.

    This is deliberately downstream of canonical replay. Running the shadow audit
    cannot change selected horizon, lifecycle ownership, BUY/SELL, or execution.
    """

    entries: list[STThesisIdentityShadow] = []
    for row in replay.rows:
        if row.entry_decision is None:
            continue
        shadow = classify_executed_st_thesis(row.snapshot, row.entry_decision)
        if shadow is not None:
            entries.append(shadow)

    family_counts = tuple(
        (family, sum(1 for entry in entries if entry.family is family))
        for family in STThesisFamily
    )
    unresolved = sum(1 for entry in entries if entry.family is STThesisFamily.UNRESOLVED)
    coverage = STThesisIdentityCoverage(
        executed_st_entries=len(entries),
        resolved_entries=len(entries) - unresolved,
        unresolved_entries=unresolved,
        family_counts=family_counts,
    )
    return STThesisIdentityShadowReport(entries=tuple(entries), coverage=coverage)


__all__ = [
    "STDefendedAnchor",
    "STDefendedAnchorKind",
    "STEconomicMission",
    "STThesisFamily",
    "STThesisIdentityCoverage",
    "STThesisIdentityShadow",
    "STThesisIdentityShadowReport",
    "audit_st_thesis_identity_shadow",
    "classify_executed_st_thesis",
]
