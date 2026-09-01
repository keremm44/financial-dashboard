from dataclasses import fields
from types import SimpleNamespace

from financial_dashboard.decision.eligibility import EligibilityAssessment, EligibilityState
from financial_dashboard.decision.entry_qualification import (
    EntryQualificationAssessment,
    ScenarioStage,
    assess_entry_qualification,
)
from financial_dashboard.decision.gate_authority import GateAuthority
from financial_dashboard.decision.gate_registry import gate_owner
from financial_dashboard.decision.scenario import EntryScenarioAssessment
from financial_dashboard.decision.target_path import TargetPathNodeState, TargetPathStatus


def _path(*, status=TargetPathStatus.READY, defended=False):
    active = None
    if status is TargetPathStatus.READY and defended:
        active = SimpleNamespace(state=TargetPathNodeState.DEFENDED)
    return SimpleNamespace(status=status, active_node=active)


def test_qualification_references_eligibility_blockers_without_storing_a_copy():
    blockers = ("VOLATILITY_SHOCK", "STABIL_LONG_ENTRY_CONTRADICTION")
    eligibility = EligibilityAssessment(
        EligibilityState.BLOCKED,
        ("HARD_GATE_ACTIVE",),
        blockers,
        (),
    )

    result = assess_entry_qualification(eligibility, target_path=_path())

    assert result.state is ScenarioStage.BLOCKED
    assert result.eligibility is eligibility
    assert result.blockers is eligibility.blockers
    assert result.waiting_for == ()
    assert "blockers" not in {item.name for item in fields(EntryQualificationAssessment)}
    assert "waiting_for" not in {item.name for item in fields(EntryQualificationAssessment)}


def test_qualification_reuses_eligibility_waits_when_target_path_is_ready():
    waiting = ("STABIL_RECOVERY_TO_CONFIRM",)
    eligibility = EligibilityAssessment(
        EligibilityState.WAITING,
        ("KNOWN_CONDITIONS_INCOMPLETE",),
        (),
        waiting,
    )

    result = assess_entry_qualification(eligibility, target_path=_path())

    assert result.state is ScenarioStage.DEVELOPING
    assert result.waiting_for is eligibility.waiting_for


def test_target_path_wait_remains_separate_from_eligibility_and_keeps_its_owner():
    eligibility = EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        ("ALL_MARKET_ELIGIBILITY_GUARDS_SATISFIED",),
        (),
        (),
    )

    result = assess_entry_qualification(
        eligibility,
        target_path=_path(status=TargetPathStatus.UNKNOWN),
    )

    assert result.state is ScenarioStage.DEVELOPING
    assert eligibility.waiting_for == ()
    assert result.target_path_waiting_for == ("TARGET_PATH_TO_RESOLVE",)
    assert result.waiting_for == ("TARGET_PATH_TO_RESOLVE",)
    assert gate_owner("TARGET_PATH_TO_RESOLVE") is GateAuthority.TARGET_PATH


def test_defended_target_path_adds_only_target_path_maturity_wait():
    eligibility = EligibilityAssessment(
        EligibilityState.ELIGIBLE,
        ("ALL_MARKET_ELIGIBILITY_GUARDS_SATISFIED",),
        (),
        (),
    )

    result = assess_entry_qualification(
        eligibility,
        target_path=_path(defended=True),
    )

    assert result.state is ScenarioStage.DEVELOPING
    assert result.target_path_waiting_for == ("ACTIVE_TARGET_PATH_NODE_DEFENDED",)
    assert result.reasons == ("TARGET_PATH_DEFENSE_REQUIRES_REASSESSMENT",)
    assert gate_owner("ACTIVE_TARGET_PATH_NODE_DEFENDED") is GateAuthority.TARGET_PATH


def test_scenario_dataclass_no_longer_stores_copied_eligibility_gate_fields():
    field_names = {item.name for item in fields(EntryScenarioAssessment)}

    assert "eligibility" in field_names
    assert "qualification" in field_names
    assert "stage" not in field_names
    assert "eligibility_state" not in field_names
    assert "blockers" not in field_names
    assert "waiting_for" not in field_names


def test_qualification_contract_has_no_permission_input_or_action_surface():
    parameter_names = tuple(
        assess_entry_qualification.__annotations__
    )

    assert "permission" not in parameter_names
    assert not hasattr(
        EntryQualificationAssessment(
            state=ScenarioStage.QUALIFIED,
            eligibility=EligibilityAssessment(
                EligibilityState.ELIGIBLE,
                (),
                (),
                (),
            ),
            target_path_status=TargetPathStatus.READY,
            target_path_waiting_for=(),
            reasons=(),
        ),
        "action",
    )
