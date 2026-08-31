from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .gate_authority import GateAuthority


class GateSemantic(StrEnum):
    HARD_BLOCK = "HARD_BLOCK"
    WAIT = "WAIT"
    DEFERRED_SUMMARY = "DEFERRED_SUMMARY"


@dataclass(frozen=True, slots=True)
class GateDefinition:
    token_pattern: str
    owner: GateAuthority
    semantic: GateSemantic
    evidence_family: str

    def __post_init__(self) -> None:
        if not self.token_pattern.strip():
            raise ValueError("gate token pattern must be non-empty")
        if not self.evidence_family.strip():
            raise ValueError("gate evidence family must be non-empty")

    @property
    def is_template(self) -> bool:
        return "{}" in self.token_pattern

    def matches(self, token: str) -> bool:
        if not self.is_template:
            return token == self.token_pattern
        escaped = re.escape(self.token_pattern).replace(re.escape("{}"), r"[^:]+")
        return re.fullmatch(escaped, token) is not None


# Registry semantics describe who owns one blocker/wait condition. They do not add a
# new veto, threshold, score or action rule. evidence_family is the lineage family
# that should explain the condition when runtime lineage is available.
GATE_REGISTRY: tuple[GateDefinition, ...] = (
    # Structure / thesis hard gates and waits.
    GateDefinition("STRUCTURE_DATA_{}", GateAuthority.STRUCTURE, GateSemantic.HARD_BLOCK, "structure"),
    GateDefinition("STRUCTURAL_DIRECTION_UNRESOLVED", GateAuthority.STRUCTURE, GateSemantic.HARD_BLOCK, "structure"),
    GateDefinition("STRUCTURAL_THESIS_{}", GateAuthority.STRUCTURE, GateSemantic.HARD_BLOCK, "structure"),
    GateDefinition("STRUCTURAL_TRANSITION_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("VALID_STRUCTURAL_AUTHORITY", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("STRUCTURAL_DIRECTION_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("ST_STRUCTURE_AUTHORITY_TO_RECOVER", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("ST_STRUCTURE_AUTHORITY_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("ST_TRANSITION_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("CANONICAL_ST_STRUCTURE_STATE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("LT_STRUCTURE_AUTHORITY_TO_RECOVER", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("LT_STRUCTURE_AUTHORITY_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("LT_TRANSITION_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("ST_STRUCTURE_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("CROSS_HORIZON_STRUCTURE_TO_RECONCILE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("CANONICAL_LT_STRUCTURE_STATE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("LOWER_HORIZON_COUNTER_MOVE_TO_RESOLVE", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),
    GateDefinition("{}:STRUCTURAL_TIMING_CONTEXT", GateAuthority.STRUCTURE, GateSemantic.WAIT, "structure"),

    # Permission owns context permission. Two summary reasons deliberately defer to
    # canonical Structure / independent Conflict owners rather than double-vetoing.
    GateDefinition("CANONICAL_STRUCTURE_UNRESOLVED", GateAuthority.STRUCTURE, GateSemantic.DEFERRED_SUMMARY, "structure"),
    GateDefinition("CONTEXT_CONFLICT_HIGH", GateAuthority.CONFLICT, GateSemantic.DEFERRED_SUMMARY, "context_conflict"),
    GateDefinition("REVERSAL_DIRECTION_UNRESOLVED", GateAuthority.PERMISSION, GateSemantic.HARD_BLOCK, "permission"),
    GateDefinition("CONTINUATION_DIRECTION_UNRESOLVED", GateAuthority.PERMISSION, GateSemantic.HARD_BLOCK, "permission"),
    GateDefinition("PERMISSION_BLOCKED", GateAuthority.PERMISSION, GateSemantic.HARD_BLOCK, "permission"),
    GateDefinition("CONTEXT_CONFLICT_TO_RESOLVE", GateAuthority.PERMISSION, GateSemantic.WAIT, "context_conflict"),
    GateDefinition("CANONICAL_STRUCTURAL_FOLLOW_THROUGH", GateAuthority.PERMISSION, GateSemantic.WAIT, "structure"),
    GateDefinition("REACTION_INTERACTION_TO_BECOME_ACTIVE", GateAuthority.PERMISSION, GateSemantic.WAIT, "reaction"),
    GateDefinition("MATERIAL_SUPPORTING_CONFLICT_TO_RESOLVE", GateAuthority.PERMISSION, GateSemantic.WAIT, "context_conflict"),
    GateDefinition("FUTURE_ACTION_LAYER_TIMING", GateAuthority.PERMISSION, GateSemantic.WAIT, "timing"),
    GateDefinition("QUALIFIED_CONTINUATION_REACTION_OR_TRANSITION_CONTEXT", GateAuthority.PERMISSION, GateSemantic.WAIT, "permission"),
    GateDefinition("PERMISSION_SCOPE_SIDE_TO_RECONCILE", GateAuthority.PERMISSION, GateSemantic.WAIT, "permission"),
    GateDefinition("PERMISSION_SIDE_TO_RESOLVE", GateAuthority.PERMISSION, GateSemantic.WAIT, "permission"),
    GateDefinition("PERMISSION_TO_OPEN", GateAuthority.PERMISSION, GateSemantic.WAIT, "permission"),
    GateDefinition("CONTEXT_CONFLICT_TO_RECONCILE", GateAuthority.CONFLICT, GateSemantic.WAIT, "context_conflict"),

    # Independent environment/opportunity/conflict/coverage gates.
    GateDefinition("VOLATILITY_SHOCK", GateAuthority.ENVIRONMENT, GateSemantic.HARD_BLOCK, "volatility"),
    GateDefinition("OPPORTUNITY_NONE", GateAuthority.OPPORTUNITY, GateSemantic.HARD_BLOCK, "targeting"),
    GateDefinition("MORE_DIRECTIONAL_ROOM", GateAuthority.OPPORTUNITY, GateSemantic.WAIT, "targeting"),
    GateDefinition("OPPORTUNITY_EVIDENCE_OR_CALIBRATION", GateAuthority.OPPORTUNITY, GateSemantic.WAIT, "targeting"),
    GateDefinition("OBSERVED_DIRECTIONAL_OPPORTUNITY", GateAuthority.OPPORTUNITY, GateSemantic.WAIT, "targeting"),
    GateDefinition("INDEPENDENT_FAMILY_CONFLICT_HIGH", GateAuthority.CONFLICT, GateSemantic.HARD_BLOCK, "independent_conflict"),
    GateDefinition("MATERIAL_CONFLICT_TO_RESOLVE", GateAuthority.CONFLICT, GateSemantic.WAIT, "independent_conflict"),
    GateDefinition("CONFLICT_EVIDENCE_TO_RESOLVE", GateAuthority.CONFLICT, GateSemantic.WAIT, "independent_conflict"),
    GateDefinition("CRITICAL_STRUCTURE_COVERAGE_MISSING", GateAuthority.COVERAGE, GateSemantic.HARD_BLOCK, "coverage"),
    GateDefinition("CRITICAL_COVERAGE:{}", GateAuthority.COVERAGE, GateSemantic.WAIT, "coverage"),

    # Setup timing and target-path maturity.
    GateDefinition("TIMING_{}", GateAuthority.TIMING, GateSemantic.WAIT, "timing"),
    GateDefinition("{}:SETUP_TRIGGER_DATA", GateAuthority.TIMING, GateSemantic.WAIT, "timing"),
    GateDefinition("NEW_SETUP_PATH", GateAuthority.TIMING, GateSemantic.WAIT, "timing"),
    GateDefinition("SETUP_TRIGGER_CONFIRMATION", GateAuthority.TIMING, GateSemantic.WAIT, "timing"),
    GateDefinition("SETUP_TRIGGER", GateAuthority.TIMING, GateSemantic.WAIT, "timing"),
    GateDefinition("TARGET_PATH_TO_RESOLVE", GateAuthority.TARGET_PATH, GateSemantic.WAIT, "target_path"),
    GateDefinition("ACTIVE_TARGET_PATH_NODE_DEFENDED", GateAuthority.TARGET_PATH, GateSemantic.WAIT, "target_path"),

    # Scenario/arbiter ownership waits.
    GateDefinition("SCENARIO_TO_QUALIFY", GateAuthority.SCENARIO, GateSemantic.WAIT, "scenario"),
    GateDefinition("LONG_TERM_SCENARIO_PRESENCE_TO_RESOLVE", GateAuthority.ARBITER, GateSemantic.WAIT, "scenario"),
    GateDefinition("SHORT_TERM_SCENARIO_PRESENCE_TO_RESOLVE", GateAuthority.ARBITER, GateSemantic.WAIT, "scenario"),

    # Fresh execution/action capability.
    GateDefinition("ACTION_SIDE_NOT_PERMITTED:{}", GateAuthority.ACTION_CAPABILITY, GateSemantic.HARD_BLOCK, "action_capability"),
    GateDefinition("{}:EXECUTION_TRIGGER_DATA", GateAuthority.EXECUTION, GateSemantic.WAIT, "execution"),
    GateDefinition("NEW_EXECUTION_EVENT", GateAuthority.EXECUTION, GateSemantic.WAIT, "execution"),
    GateDefinition("FRESH_EXECUTION_EVENT", GateAuthority.EXECUTION, GateSemantic.WAIT, "execution"),
    GateDefinition("FRESH_LONG_EXIT_EXECUTION_EVENT", GateAuthority.EXECUTION, GateSemantic.WAIT, "execution"),
    GateDefinition("{}:LONG_EXIT_EXECUTION_DATA", GateAuthority.EXECUTION, GateSemantic.WAIT, "execution"),
    GateDefinition("NEW_LONG_EXIT_EXECUTION_EVENT", GateAuthority.EXECUTION, GateSemantic.WAIT, "execution"),

    # Persistent position ownership recovery is not a market-direction gate.
    GateDefinition("POSITION_ENTRY_METADATA_TO_RECOVER", GateAuthority.POSITION_LIFECYCLE, GateSemantic.WAIT, "position_lifecycle"),
)


def _assert_registry_is_unambiguous() -> None:
    patterns = [item.token_pattern for item in GATE_REGISTRY]
    if len(patterns) != len(set(patterns)):
        raise RuntimeError("gate registry contains duplicate token patterns")


_assert_registry_is_unambiguous()


def gate_definition(token: str) -> GateDefinition | None:
    normalized = str(token).strip()
    exact = tuple(item for item in GATE_REGISTRY if not item.is_template and item.matches(normalized))
    if exact:
        return exact[0]
    templates = tuple(item for item in GATE_REGISTRY if item.is_template and item.matches(normalized))
    if len(templates) > 1:
        raise RuntimeError(f"gate token matches multiple registry templates: {normalized}")
    return templates[0] if templates else None


def unregistered_gate_tokens(tokens) -> tuple[str, ...]:
    return tuple(sorted({str(token) for token in tokens if gate_definition(str(token)) is None}))


def gate_owner(token: str) -> GateAuthority | None:
    definition = gate_definition(token)
    return None if definition is None else definition.owner


__all__ = [
    "GATE_REGISTRY",
    "GateDefinition",
    "GateSemantic",
    "gate_definition",
    "gate_owner",
    "unregistered_gate_tokens",
]
