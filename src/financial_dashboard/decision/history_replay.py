from __future__ import annotations

from financial_dashboard.data.parquet_store import ParquetOHLCVStore

from .history_single_pass import SinglePassHistoricalDecisionInputReplayRunner
from .persistent_history_runner import PersistentHistoricalDecisionInputReplayRunner


class HistoricalDecisionInputReplayRunner(PersistentHistoricalDecisionInputReplayRunner):
    """Canonical historical decision-input runner.

    Historical replay uses append-only native/supporting engine checkpoints plus one
    frozen decision read-model timeline. The append checkpoint itself is metadata-only,
    so historical snapshots are not serialized twice.
    """

    def __init__(self, store: ParquetOHLCVStore) -> None:
        super().__init__(store)


LegacyHistoricalDecisionInputReplayRunner = SinglePassHistoricalDecisionInputReplayRunner


__all__ = [
    "HistoricalDecisionInputReplayRunner",
    "LegacyHistoricalDecisionInputReplayRunner",
]
