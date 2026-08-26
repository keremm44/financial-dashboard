from __future__ import annotations

from financial_dashboard.data.parquet_store import ParquetOHLCVStore

from .history_incremental import IncrementalHistoricalDecisionInputReplayRunner
from .history_single_pass import SinglePassHistoricalDecisionInputReplayRunner


class HistoricalDecisionInputReplayRunner(IncrementalHistoricalDecisionInputReplayRunner):
    """Canonical historical decision-input runner.

    Historical replay now uses the same append-only causal reducer and native-domain
    runtime intended for live/catch-up execution. The old capture-based single-pass
    implementation is intentionally retained only as an explicit equivalence/debug
    fallback while the migration settles.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        super().__init__(store)


LegacyHistoricalDecisionInputReplayRunner = SinglePassHistoricalDecisionInputReplayRunner


__all__ = [
    "HistoricalDecisionInputReplayRunner",
    "LegacyHistoricalDecisionInputReplayRunner",
]
