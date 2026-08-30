from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.decision_audit.domain_lock_research import _classify_locks


def _assessment(
    *,
    direction="LONG",
    thesis="INTACT",
    quality="VALID",
    permission_gate="OPEN",
    permission_side="LONG",
    blockers=(),
    waiting=(),
):
    return SimpleNamespace(
        structural=SimpleNamespace(
            direction=SimpleNamespace(value=direction),
            thesis_state=SimpleNamespace(value=thesis),
            data_quality=SimpleNamespace(value=quality),
        ),
        permission=SimpleNamespace(
            gate_state=SimpleNamespace(value=permission_gate),
            permitted_side=SimpleNamespace(value=permission_side),
            blocking_reasons=(),
            waiting_for=(),
        ),
        eligibility=SimpleNamespace(
            blockers=tuple(blockers),
            waiting_for=tuple(waiting),
        ),
    )


def _scenario(*, presence="PRESENT", waiting=(), reasons=()):
    return SimpleNamespace(
        presence=SimpleNamespace(value=presence),
        waiting_for=tuple(waiting),
        reasons=tuple(reasons),
    )


def test_absent_st_with_short_structure_identifies_structure_lock():
    locks, evidence = _classify_locks(
        _assessment(direction="SHORT"),
        _scenario(presence="ABSENT", reasons=("LONG_ENTRY_REQUIRES_LONG_STRUCTURE",)),
    )

    assert locks == ("STRUCTURE",)
    assert "STRUCTURE_DIRECTION:SHORT" in evidence


def test_long_structure_keeps_opportunity_and_setup_as_separate_lock_candidates():
    locks, evidence = _classify_locks(
        _assessment(
            blockers=("OPPORTUNITY_NONE",),
            waiting=("SETUP_TRIGGER_CONFIRMATION",),
        ),
        _scenario(waiting=("TARGET_PATH_TO_RESOLVE",)),
    )

    assert "TARGETING_OPPORTUNITY" in locks
    assert "SETUP_TIMING" in locks
    assert "OPPORTUNITY_NONE" in evidence
    assert "SETUP_TRIGGER_CONFIRMATION" in evidence
    assert "TARGET_PATH_TO_RESOLVE" in evidence
