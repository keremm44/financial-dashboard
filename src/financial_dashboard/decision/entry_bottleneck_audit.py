from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .scenario import EntryScenarioAssessment, ScenarioPresence, ScenarioStage


class EntryBottleneckFamily(StrEnum):
    """Diagnostic-only ownership buckets for canonical entry waits/blockers.

    These buckets do not participate in trading decisions. They classify the exact
    canonical blocker/wait tokens already emitted by Scenario/Eligibility so replay
    reports can show overlap such as TIMING+PERMISSION without changing policy.
    """

    STRUCTURE = "STRUCTURE"
    PERMISSION = "PERMISSION"
    TIMING = "TIMING"
    OPPORTUNITY = "OPPORTUNITY"
    CONFLICT = "CONFLICT"
    TARGET_PATH = "TARGET_PATH"
    STABIL = "STABIL"
    COVERAGE = "COVERAGE"
    ENVIRONMENT = "ENVIRONMENT"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class EntryBottleneckAttribution:
    families: tuple[EntryBottleneckFamily, ...]
    tokens: tuple[str, ...]

    @property
    def label(self) -> str:
        if not self.families:
            return "NONE"
        return "+".join(family.value for family in self.families)

    @property
    def is_single_family(self) -> bool:
        return len(self.families) == 1


def _family_for_token(token: str) -> EntryBottleneckFamily:
    value = str(token).strip().upper()

    if (
        value in {
            "SETUP_TRIGGER",
            "SETUP_TRIGGER_CONFIRMATION",
            "NEW_SETUP_PATH",
            "LOWER_HORIZON_COUNTER_MOVE_TO_RESOLVE",
        }
        or value.endswith(":SETUP_TRIGGER_DATA")
        or value.endswith(":STRUCTURAL_TIMING_CONTEXT")
    ):
        return EntryBottleneckFamily.TIMING

    if (
        value.startswith("PERMISSION_")
        or value in {
            "CONTEXT_CONFLICT_TO_RECONCILE",
            "CANONICAL_STRUCTURAL_FOLLOW_THROUGH",
            "QUALIFIED_CONTINUATION_REACTION_OR_TRANSITION_CONTEXT",
            "REACTION_INTERACTION_TO_BECOME_ACTIVE",
        }
    ):
        return EntryBottleneckFamily.PERMISSION

    if value in {
        "MORE_DIRECTIONAL_ROOM",
        "OPPORTUNITY_EVIDENCE_OR_CALIBRATION",
        "OBSERVED_DIRECTIONAL_OPPORTUNITY",
        "OPPORTUNITY_NONE",
    }:
        return EntryBottleneckFamily.OPPORTUNITY

    if value in {
        "MATERIAL_CONFLICT_TO_RESOLVE",
        "CONFLICT_EVIDENCE_TO_RESOLVE",
        "INDEPENDENT_FAMILY_CONFLICT_HIGH",
    }:
        return EntryBottleneckFamily.CONFLICT

    if value.startswith("TARGET_PATH_") or value == "ACTIVE_TARGET_PATH_NODE_DEFENDED":
        return EntryBottleneckFamily.TARGET_PATH

    if value.startswith("STABIL_"):
        return EntryBottleneckFamily.STABIL

    if value.startswith("CRITICAL_COVERAGE:") or value == "CRITICAL_STRUCTURE_COVERAGE_MISSING":
        return EntryBottleneckFamily.COVERAGE

    if value == "VOLATILITY_SHOCK":
        return EntryBottleneckFamily.ENVIRONMENT

    if (
        value.startswith("STRUCTURE_DATA_")
        or value.startswith("STRUCTURAL_")
        or value == "VALID_STRUCTURAL_AUTHORITY"
    ):
        return EntryBottleneckFamily.STRUCTURE

    return EntryBottleneckFamily.OTHER


def attribute_entry_bottlenecks(
    scenario: EntryScenarioAssessment,
) -> EntryBottleneckAttribution:
    """Classify canonical waits/blockers without counterfactual policy changes."""

    if scenario.presence is not ScenarioPresence.PRESENT:
        return EntryBottleneckAttribution((), ())
    if scenario.stage is ScenarioStage.QUALIFIED:
        return EntryBottleneckAttribution((), ())

    tokens = tuple(dict.fromkeys((*scenario.blockers, *scenario.waiting_for)))
    families = tuple(
        sorted(
            {_family_for_token(token) for token in tokens},
            key=lambda family: family.value,
        )
    )
    return EntryBottleneckAttribution(families=families, tokens=tokens)


def diagnostic_episode_key(
    scenario: EntryScenarioAssessment,
) -> tuple[str, str] | None:
    """Return a conservative contiguous-episode proxy, not persisted setup identity.

    The key deliberately uses only already-observed scenario kind and active target
    context. It must never be used by trading policy or persistence.
    """

    if scenario.presence is not ScenarioPresence.PRESENT:
        return None
    target = scenario.active_target_identity
    if target is None or not str(target).strip():
        return None
    return scenario.kind.value, str(target).strip()


__all__ = [
    "EntryBottleneckAttribution",
    "EntryBottleneckFamily",
    "attribute_entry_bottlenecks",
    "diagnostic_episode_key",
]
