from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .gate_authority import GateAuthority
from .gate_registry import gate_owner
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


_AUTHORITY_TO_FAMILY: dict[GateAuthority, EntryBottleneckFamily] = {
    GateAuthority.STRUCTURE: EntryBottleneckFamily.STRUCTURE,
    GateAuthority.PERMISSION: EntryBottleneckFamily.PERMISSION,
    GateAuthority.TIMING: EntryBottleneckFamily.TIMING,
    GateAuthority.OPPORTUNITY: EntryBottleneckFamily.OPPORTUNITY,
    GateAuthority.CONFLICT: EntryBottleneckFamily.CONFLICT,
    GateAuthority.TARGET_PATH: EntryBottleneckFamily.TARGET_PATH,
    GateAuthority.STABIL: EntryBottleneckFamily.STABIL,
    GateAuthority.COVERAGE: EntryBottleneckFamily.COVERAGE,
    GateAuthority.ENVIRONMENT: EntryBottleneckFamily.ENVIRONMENT,
}


def _family_for_token(token: str) -> EntryBottleneckFamily:
    """Use the canonical gate registry instead of a second ownership table."""

    owner = gate_owner(str(token).strip())
    if owner is None:
        return EntryBottleneckFamily.OTHER
    return _AUTHORITY_TO_FAMILY.get(owner, EntryBottleneckFamily.OTHER)


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
