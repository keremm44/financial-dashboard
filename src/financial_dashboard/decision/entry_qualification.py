from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .eligibility import EligibilityAssessment, EligibilityState
from .target_path import TargetPath, TargetPathNodeState, TargetPathStatus


class ScenarioStage(StrEnum):
    """Qualification state for an observed entry scenario."""

    QUALIFIED = "QUALIFIED"
    DEVELOPING = "DEVELOPING"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class EntryQualificationAssessment:
    """Gate view for one already-observed entry scenario.

    Eligibility remains the canonical owner of technical market blockers and waits.
    This layer adds only target-path maturity and derives the scenario qualification
    stage. It does not reinterpret Permission, Structure, Stabil, or any other gate
    family and it cannot emit a trading action.
    """

    state: ScenarioStage
    eligibility: EligibilityAssessment
    target_path_status: TargetPathStatus
    target_path_waiting_for: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def blockers(self) -> tuple[str, ...]:
        """Expose Eligibility-owned blockers without storing a second copy."""

        return self.eligibility.blockers

    @property
    def waiting_for(self) -> tuple[str, ...]:
        """Combine Eligibility waits with TargetPath-owned maturity waits."""

        if not self.target_path_waiting_for:
            return self.eligibility.waiting_for
        if not self.eligibility.waiting_for:
            return self.target_path_waiting_for
        return tuple(
            dict.fromkeys(
                (*self.eligibility.waiting_for, *self.target_path_waiting_for)
            )
        )


def assess_entry_qualification(
    eligibility: EligibilityAssessment,
    *,
    target_path: TargetPath,
) -> EntryQualificationAssessment:
    """Derive entry qualification without duplicating technical gate ownership."""

    target_waiting: list[str] = []
    reasons: list[str] = []

    active = target_path.active_node
    if target_path.status is not TargetPathStatus.READY:
        target_waiting.append("TARGET_PATH_TO_RESOLVE")
    elif active is not None and active.state is TargetPathNodeState.DEFENDED:
        target_waiting.append("ACTIVE_TARGET_PATH_NODE_DEFENDED")
        reasons.append("TARGET_PATH_DEFENSE_REQUIRES_REASSESSMENT")

    if eligibility.state is EligibilityState.BLOCKED:
        state = ScenarioStage.BLOCKED
    elif eligibility.state is EligibilityState.WAITING or target_waiting:
        state = ScenarioStage.DEVELOPING
    else:
        state = ScenarioStage.QUALIFIED

    return EntryQualificationAssessment(
        state=state,
        eligibility=eligibility,
        target_path_status=target_path.status,
        target_path_waiting_for=tuple(dict.fromkeys(target_waiting)),
        reasons=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "EntryQualificationAssessment",
    "ScenarioStage",
    "assess_entry_qualification",
]
