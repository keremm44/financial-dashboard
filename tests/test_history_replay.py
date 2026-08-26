from __future__ import annotations

from financial_dashboard.decision.history_incremental import IncrementalHistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_replay import (
    HistoricalDecisionInputReplayRunner,
    LegacyHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.history_single_pass import SinglePassHistoricalDecisionInputReplayRunner


def test_canonical_historical_runner_uses_incremental_causal_timeline() -> None:
    assert issubclass(HistoricalDecisionInputReplayRunner, IncrementalHistoricalDecisionInputReplayRunner)


def test_legacy_historical_runner_remains_explicit_debug_fallback() -> None:
    assert LegacyHistoricalDecisionInputReplayRunner is SinglePassHistoricalDecisionInputReplayRunner
