from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GateAuthority(StrEnum):
    STRUCTURE = "STRUCTURE"
    PERMISSION = "PERMISSION"
    ENVIRONMENT = "ENVIRONMENT"
    OPPORTUNITY = "OPPORTUNITY"
    CONFLICT = "CONFLICT"
    COVERAGE = "COVERAGE"
    TIMING = "TIMING"


@dataclass(frozen=True, slots=True)
class HardGateOwnership:
    rule: str
    owner: GateAuthority


HARD_GATE_OWNERSHIP: tuple[HardGateOwnership, ...] = (
    HardGateOwnership("STRUCTURE_DATA", GateAuthority.STRUCTURE),
    HardGateOwnership("STRUCTURAL_DIRECTION_UNRESOLVED", GateAuthority.STRUCTURE),
    HardGateOwnership("STRUCTURAL_THESIS", GateAuthority.STRUCTURE),
    HardGateOwnership("PERMISSION_BLOCKED", GateAuthority.PERMISSION),
    HardGateOwnership("VOLATILITY_SHOCK", GateAuthority.ENVIRONMENT),
    HardGateOwnership("OPPORTUNITY_NONE", GateAuthority.OPPORTUNITY),
    HardGateOwnership("INDEPENDENT_FAMILY_CONFLICT_HIGH", GateAuthority.CONFLICT),
    HardGateOwnership("CRITICAL_STRUCTURE_COVERAGE_MISSING", GateAuthority.COVERAGE),
)


# Permission can summarize facts that already have a canonical hard-gate owner in
# the Decision layer. These reasons remain useful diagnostics, but Eligibility must
# not count them as a second independent veto.
_PERMISSION_DEFERRED_OWNERS: dict[str, GateAuthority] = {
    "CANONICAL_STRUCTURE_UNRESOLVED": GateAuthority.STRUCTURE,
    "CONTEXT_CONFLICT_HIGH": GateAuthority.CONFLICT,
}


def deferred_permission_blocker_owner(reason: str) -> GateAuthority | None:
    return _PERMISSION_DEFERRED_OWNERS.get(reason)


__all__ = [
    "GateAuthority",
    "HARD_GATE_OWNERSHIP",
    "HardGateOwnership",
    "deferred_permission_blocker_owner",
]
