from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from financial_dashboard.context.envelope import FactRef

from .lifecycle import ExitStage, PositionState, TradeLifecycleState
from .st_exit_intent import STExitFamily
from .st_harvest import (
    STHarvestShadowAssessment,
    STHarvestShadowState,
    assess_st_harvest_shadow,
)
from .structural import DecisionHorizon
from .trade_exit import LongExitAssessment, PositionHealth

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


@dataclass(frozen=True, slots=True)
class STCanonicalExitAssessment:
    """Canonical economic exit result for one already-open ST trade.

    The assessment answers *why* the current ST trade should be held or terminated.
    Execution urgency belongs to Step 9 and is deliberately evaluated separately.
    """

    exit_family: STExitFamily | None
    stage: ExitStage
    position_health: PositionHealth
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_refs: tuple[FactRef, ...]
    source_lineage: tuple[str, ...]
    shadow: STHarvestShadowAssessment

    @property
    def terminal(self) -> bool:
        return self.exit_family is not None


_HOLD_HEALTHY_STATES = frozenset(
    {
        STHarvestShadowState.HOLD_MISSION_ACTIVE,
        STHarvestShadowState.HOLD_PROGRESS,
        STHarvestShadowState.HOLD_CONTINUATION,
        STHarvestShadowState.HOLD_HEALTHY_BASE,
    }
)


def _canonical_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    values = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def _lineage_from_refs(refs: Iterable[FactRef]) -> tuple[str, ...]:
    values: list[str] = []
    for ref in refs:
        lineage_id = getattr(ref, "lineage_id", None)
        if lineage_id:
            values.append(str(lineage_id))
            continue
        domain = getattr(getattr(ref, "domain", None), "value", None)
        timeframe = getattr(ref, "timeframe", None)
        native_id = getattr(ref, "native_id", None)
        if domain and timeframe and native_id:
            values.append(f"{domain}:{timeframe}:{native_id}")
    return tuple(sorted(set(values)))


def _terminal_result(
    *,
    family: STExitFamily,
    reasons: Iterable[str],
    shadow: STHarvestShadowAssessment,
    persisted_lineage: Iterable[str] = (),
) -> STCanonicalExitAssessment:
    refs = _canonical_refs(shadow.source_refs)
    lineage = tuple(sorted(set((*persisted_lineage, *_lineage_from_refs(refs)))))
    return STCanonicalExitAssessment(
        exit_family=family,
        stage=ExitStage.EXIT_READY,
        position_health=(
            PositionHealth.PRESSURED
            if family is STExitFamily.PROTECTIVE_EXIT
            else PositionHealth.PROTECTED
        ),
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
        waiting_for=(),
        source_refs=refs,
        source_lineage=lineage,
        shadow=shadow,
    )


def _hold_result(shadow: STHarvestShadowAssessment) -> STCanonicalExitAssessment:
    refs = _canonical_refs(shadow.source_refs)
    health = (
        PositionHealth.HEALTHY
        if shadow.state in _HOLD_HEALTHY_STATES
        else PositionHealth.UNKNOWN
    )
    return STCanonicalExitAssessment(
        exit_family=None,
        stage=ExitStage.MONITOR,
        position_health=health,
        reasons=tuple(
            dict.fromkeys(
                (
                    f"ST_CANONICAL_ECONOMIC_HOLD:{shadow.state.value}",
                    *shadow.reasons,
                )
            )
        ),
        waiting_for=(),
        source_refs=refs,
        source_lineage=_lineage_from_refs(refs),
        shadow=shadow,
    )


def assess_st_canonical_exit(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
) -> STCanonicalExitAssessment:
    """Activate the frozen Step-8 ST economic exit hierarchy.

    Precedence is inherited from the Step-5/6 causal policy shadow:
    thesis invalidation -> PROTECTIVE_EXIT; productive/healthy/uncertain evidence ->
    HOLD; full CONSUMED story -> PROFIT_HARVEST. UNKNOWN never becomes confirmation.

    Step-7 terminal intent is monotonic. A previously committed HARVEST survives a
    later HOLD/uncertain evaluation and may escalate to PROTECTIVE. A previously
    committed PROTECTIVE intent can never be downgraded inside the same trade.
    """

    if state.position is not PositionState.OPEN:
        raise ValueError("canonical ST exit policy requires OPEN lifecycle ownership")
    metadata = state.entry_metadata
    if metadata is None or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("canonical ST exit policy requires short-term entry ownership")

    shadow = assess_st_harvest_shadow(snapshot, state)
    existing = state.st_exit_intent

    if existing is not None and existing.family is STExitFamily.PROTECTIVE_EXIT:
        return _terminal_result(
            family=STExitFamily.PROTECTIVE_EXIT,
            reasons=existing.reasons,
            shadow=shadow,
            persisted_lineage=existing.source_lineage,
        )

    if existing is not None and existing.family is STExitFamily.PROFIT_HARVEST:
        if shadow.state is STHarvestShadowState.PROTECTIVE_PRECEDENCE:
            return _terminal_result(
                family=STExitFamily.PROTECTIVE_EXIT,
                reasons=shadow.reasons,
                shadow=shadow,
            )
        return _terminal_result(
            family=STExitFamily.PROFIT_HARVEST,
            reasons=existing.reasons,
            shadow=shadow,
            persisted_lineage=existing.source_lineage,
        )

    if shadow.state is STHarvestShadowState.PROTECTIVE_PRECEDENCE:
        return _terminal_result(
            family=STExitFamily.PROTECTIVE_EXIT,
            reasons=shadow.reasons,
            shadow=shadow,
        )

    if shadow.state is STHarvestShadowState.PROFIT_HARVEST:
        return _terminal_result(
            family=STExitFamily.PROFIT_HARVEST,
            reasons=shadow.reasons,
            shadow=shadow,
        )

    return _hold_result(shadow)


def as_long_exit_assessment(
    assessment: STCanonicalExitAssessment,
) -> LongExitAssessment:
    """Adapt the economic ST stage to the generic execution-stage shape."""

    return LongExitAssessment(
        stage=assessment.stage,
        position_health=assessment.position_health,
        reasons=assessment.reasons,
        waiting_for=assessment.waiting_for,
        source_refs=assessment.source_refs,
    )


__all__ = [
    "STCanonicalExitAssessment",
    "as_long_exit_assessment",
    "assess_st_canonical_exit",
]
