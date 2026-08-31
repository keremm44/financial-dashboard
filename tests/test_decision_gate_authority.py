from financial_dashboard.decision.gate_authority import (
    GateAuthority,
    HARD_GATE_OWNERSHIP,
    deferred_permission_blocker_owner,
)


def test_hard_gate_rules_have_one_declared_owner():
    rules = [item.rule for item in HARD_GATE_OWNERSHIP]

    assert len(rules) == len(set(rules))
    assert {item.owner for item in HARD_GATE_OWNERSHIP} == {
        GateAuthority.STRUCTURE,
        GateAuthority.PERMISSION,
        GateAuthority.ENVIRONMENT,
        GateAuthority.OPPORTUNITY,
        GateAuthority.CONFLICT,
        GateAuthority.COVERAGE,
    }


def test_permission_summary_reasons_defer_to_canonical_decision_owners():
    assert deferred_permission_blocker_owner("CANONICAL_STRUCTURE_UNRESOLVED") is GateAuthority.STRUCTURE
    assert deferred_permission_blocker_owner("CONTEXT_CONFLICT_HIGH") is GateAuthority.CONFLICT
    assert deferred_permission_blocker_owner("REVERSAL_DIRECTION_UNRESOLVED") is None
    assert deferred_permission_blocker_owner("PERMISSION_BLOCKED") is None
