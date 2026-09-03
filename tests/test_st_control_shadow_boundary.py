from __future__ import annotations

from dataclasses import fields

from financial_dashboard.decision.engine import HorizonDecisionAssessment, PreparedHorizonAssessment
from financial_dashboard.decision_input import DecisionInputSnapshot


def test_tur2_control_is_not_wired_into_canonical_decision_contracts() -> None:
    for contract in (DecisionInputSnapshot, PreparedHorizonAssessment, HorizonDecisionAssessment):
        names = {item.name for item in fields(contract)}
        assert "control" not in names
        assert "short_term_control" not in names
        assert "st_control" not in names
