from __future__ import annotations

import pandas as pd

from financial_dashboard.volatility_mtf_replay import VolatilityMTFReplay, direction_lag_records


def volatility_latest_frame(replay: VolatilityMTFReplay) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for timeframe in replay.timeframes:
        latest = replay.for_timeframe(timeframe).latest
        if latest is None:
            continue
        core_state = "" if latest.core_result is None else latest.core_result.state
        export = latest.confirmed_export
        rows.append(
            {
                "Timeframe": timeframe,
                "Timestamp": latest.timestamp,
                "Early": latest.early.state.value,
                "Evidence": latest.early.evidence_count,
                "Confirmed state": core_state,
                "Fib state": export.fib_state,
                "Coherence": export.coherence,
                "Quality": export.quality,
            }
        )
    return pd.DataFrame(rows)


def volatility_lag_frame(replay: VolatilityMTFReplay) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Timeframe": row.timeframe,
                "Direction": row.direction,
                "Early index": row.early_index,
                "Candidate index": row.candidate_index,
                "Confirmed index": row.confirmed_index,
                "Candidate lag bars": row.candidate_lag_bars,
                "Confirmed lag bars": row.confirmed_lag_bars,
            }
            for row in direction_lag_records(replay)
        ]
    )


__all__ = ["volatility_lag_frame", "volatility_latest_frame"]
