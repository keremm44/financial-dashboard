import pandas as pd

from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.st_setup_continuity import (
    STClosedMovementRecord,
    STMovementRiskBoundary,
    STSetupCandidate,
    _movement_signature_candidate,
    _movement_signature_closed,
    _new_risk_boundary,
)
from financial_dashboard.decision.st_thesis_identity import (
    STDefendedAnchor,
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
)


def test_same_risk_geometry_is_not_novel_when_only_native_identity_or_kind_changes():
    previous_risk = STMovementRiskBoundary(
        kind="EARNED_DEFENSE",
        identity="earned:old",
        timeframe="1h",
        low=104.0,
        high=105.0,
    )
    candidate_anchor = STDefendedAnchor(
        kind=STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT,
        identity="sr:new-native-id",
        timeframe="1h",
        low=104.0,
        high=105.0,
        source_refs=(),
    )

    assert _new_risk_boundary(
        candidate_anchor,
        previous_risk,
        has_new_information=True,
    ) is False

    candidate = STSetupCandidate(
        family=STThesisFamily.BREAKOUT_ACCEPTANCE,
        economic_mission=STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
        defended_anchor=candidate_anchor,
        target_identity="target:st:1",
        source_refs=(),
        reasons=("TEST_CANDIDATE",),
    )
    previous = STClosedMovementRecord(
        trade_id="trade:old",
        entry_as_of=pd.Timestamp("2026-01-05 10:00"),
        exit_as_of=pd.Timestamp("2026-01-05 12:00"),
        exit_family=STExitFamily.PROFIT_HARVEST,
        thesis_family=STThesisFamily.BREAKOUT_ACCEPTANCE,
        economic_mission=STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
        initial_risk=previous_risk,
        terminal_risk=previous_risk,
        initial_target_identity="target:st:1",
    )

    assert _movement_signature_candidate(candidate) == _movement_signature_closed(previous)
