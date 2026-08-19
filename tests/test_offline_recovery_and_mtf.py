from __future__ import annotations

from datetime import datetime

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.provider import MarketDataProvider
from financial_dashboard.data.schema import canonicalize_ohlcv
from financial_dashboard.mtf_replay import CachedMarketStructureMTFRunner


class _MutableProvider(MarketDataProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame.copy()

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        work = self.frame.copy()
        timestamps = pd.to_datetime(work["timestamp"])
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize(timestamps.dt.tz)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize(timestamps.dt.tz)
        work = work[(timestamps >= start_ts) & (timestamps <= end_ts)].copy()
        return canonicalize_ohlcv(work, symbol=symbol, timeframe=timeframe, source="fixture")


def _bist_5m(days: int = 12) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_idx, day in enumerate(pd.bdate_range("2026-07-20", periods=days)):
        start = pd.Timestamp(day.date()).tz_localize("Europe/Istanbul") + pd.Timedelta(hours=10)
        for bar_idx in range(96):
            ts = start + pd.Timedelta(minutes=5 * bar_idx)
            base = 100.0 + day_idx * 0.6 + ((bar_idx % 24) - 12) * 0.07
            rows.append(
                {
                    "timestamp": ts,
                    "open": base,
                    "high": base + 0.55,
                    "low": base - 0.50,
                    "close": base + (0.16 if bar_idx % 3 else -0.10),
                    "volume": 1000.0 + day_idx * 40 + bar_idx * 5,
                    "is_closed": True,
                    "is_complete": True,
                }
            )
    return pd.DataFrame(rows)


def _summary(run) -> dict[str, tuple[tuple[object, str, int, float | None, tuple[str, ...]], ...]]:
    return {
        timeframe: tuple(
            (result.timestamp, result.state, int(result.direction), result.score, result.events)
            for result in replay.results
        )
        for timeframe, replay in run.replays.items()
    }


def test_single_5m_revision_only_changes_containing_derived_buckets(tmp_path) -> None:
    source = _bist_5m(days=1)
    provider = _MutableProvider(source)
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)
    start = datetime.fromisoformat("2026-07-20T10:00:00+03:00")
    end = datetime.fromisoformat("2026-07-20T18:00:00+03:00")

    first = pipeline.refresh_bist_5m(
        symbol="THYAO",
        start=start,
        end=end,
        target_timeframes=("15m", "1h", "4h", "1d"),
    )
    before = {tf: frame.copy(deep=True).set_index("timestamp") for tf, frame in first.derived.items()}

    revised_ts = pd.Timestamp("2026-07-20T10:20:00+03:00")
    mask = pd.to_datetime(provider.frame["timestamp"]) == revised_ts
    provider.frame.loc[mask, ["high", "close", "volume"]] = [150.0, 149.0, 9999.0]

    second = pipeline.refresh_bist_5m(
        symbol="THYAO",
        start=start,
        end=end,
        target_timeframes=("15m", "1h", "4h", "1d"),
    )
    after = {tf: frame.copy(deep=True).set_index("timestamp") for tf, frame in second.derived.items()}

    affected = {
        "15m": pd.Timestamp("2026-07-20T10:15:00+03:00"),
        "1h": pd.Timestamp("2026-07-20T10:00:00+03:00"),
        "4h": pd.Timestamp("2026-07-20T10:00:00+03:00"),
        "1d": pd.Timestamp("2026-07-20T10:00:00+03:00"),
    }
    for timeframe, bucket in affected.items():
        assert before[timeframe].drop(index=bucket).equals(after[timeframe].drop(index=bucket))
        assert not before[timeframe].loc[bucket].equals(after[timeframe].loc[bucket])


def test_parquet_cache_recovers_after_fresh_store_and_runner_instances(tmp_path) -> None:
    provider = _MutableProvider(_bist_5m())
    first_store = ParquetOHLCVStore(tmp_path)
    first_pipeline = MarketDataPipeline(provider, first_store)
    first_pipeline.refresh_bist_5m(
        symbol="THYAO",
        start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-04T18:00:00+03:00"),
    )

    first_run = CachedMarketStructureMTFRunner(first_store).run(symbol="THYAO")

    restarted_store = ParquetOHLCVStore(tmp_path)
    restarted_run = CachedMarketStructureMTFRunner(restarted_store).run(symbol="THYAO")

    assert first_run.timeframes == restarted_run.timeframes
    assert _summary(first_run) == _summary(restarted_run)
    for timeframe in first_run.timeframes:
        assert len(restarted_run.replays[timeframe].input_batch.frame) > 0


def test_cached_mtf_runner_replays_all_timeframes_independently(tmp_path) -> None:
    provider = _MutableProvider(_bist_5m())
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)
    pipeline.refresh_bist_5m(
        symbol="THYAO",
        start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-04T18:00:00+03:00"),
    )

    runner = CachedMarketStructureMTFRunner(store)
    first = runner.run(symbol="THYAO")
    second = runner.run(symbol="THYAO")

    assert first.timeframes == ("15m", "30m", "1h", "2h", "4h", "1d")
    assert set(first.replays) == set(first.timeframes)
    assert _summary(first) == _summary(second)
    for timeframe, replay in first.replays.items():
        assert replay.input_batch.frame["timeframe"].eq(timeframe).all()
        assert len(replay.results) == len(replay.input_batch.frame)
        assert replay.snapshot is not None
