from financial_dashboard.decision_audit.scenario_authority_research import _diagnosis


def test_diagnosis_marks_opportunity_when_st_never_forms():
    assert (
        _diagnosis(
            snapshots=30,
            st_present=0,
            st_qualified=0,
            lt_present=10,
            lt_qualified=5,
            suppressed=0,
            opportunity_absent=8,
            opportunity_unknown=4,
        )
        == "ST_SCENARIO_NOT_FORMED_OPPORTUNITY_DOMINANT"
    )


def test_diagnosis_marks_lt_priority_when_st_develops_but_never_qualifies():
    assert (
        _diagnosis(
            snapshots=30,
            st_present=20,
            st_qualified=0,
            lt_present=25,
            lt_qualified=10,
            suppressed=12,
            opportunity_absent=0,
            opportunity_unknown=0,
        )
        == "ST_DEVELOPED_NOT_QUALIFIED_WITH_LT_PRIORITY"
    )


def test_diagnosis_does_not_blame_authority_without_evidence():
    assert (
        _diagnosis(
            snapshots=30,
            st_present=20,
            st_qualified=8,
            lt_present=10,
            lt_qualified=2,
            suppressed=0,
            opportunity_absent=0,
            opportunity_unknown=0,
        )
        == "SCENARIO_AUTHORITY_NOT_PRIMARY_BOTTLENECK"
    )
