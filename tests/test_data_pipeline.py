from __future__ import annotations

from datetime import datetime

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.data.provider import MarketDataProvider
from financial_dashboard.data.resampler import ResamplePolicy
from financial_dashboard.data.schema import canonicalize_ohlcv
from financial_dashboard.engines.market_structure import MarketStructureConfig
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine


class _Provider(MarketDataProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0
        self.timeframes: list[str] = []

    def get_ohlcv(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        self.calls += 1
        self.timeframes.append(timeframe)
        return canonicalize_ohlcv(
            self.frame,
            symbol=symbol,
            timeframe=timeframe,
            source="fixture",
        )


def _base_frame() -> pd.DataFrame:
    ts = pd.date_range("2026-08-19 10:00:00+03:00", periods=6, freq="5min")
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": [100, 101, 102, 103, 104, 105],
            "high": [101, 102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103, 104],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "volume": [10, 20, 30, 40, 50, 60],
        }
    )


def _multi_day_bist(minutes: int, bars_per_day: int) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    trading_days = pd.bdate_range("2026-07-20", periods=12)
    k = 0
    for day_idx, day in enumerate(trading_days):
        session_start = pd.Timestamp(day.date()).tz_localize("Europe/Istanbul") + pd.Timedelta(hours=10)
        for bar_idx in range(bars_per_day):
            ts = session_start + pd.Timedelta(minutes=minutes * bar_idx)
            wave = ((bar_idx % max(4, bars_per_day // 4)) - max(2, bars_per_day // 8)) * 0.08
            day_wave = ((day_idx % 4) - 1.5) * 0.7
            trend = day_idx * 0.45
            base = 100.0 + trend + day_wave + wave
            rows.append(
                {
                    "timestamp": ts,
                    "open": base,
                    "high": base + 0.55 + (0.05 if bar_idx % 7 == 0 else 0.0),
                    "low": base - 0.50 - (0.05 if bar_idx % 11 == 0 else 0.0),
                    "close": base + (0.18 if (k % 3) else -0.12),
                    "volume": 1000.0 + (bar_idx * 7) + (day_idx * 50),
                }
            )
            k += 1
    return pd.DataFrame(rows)


def _multi_day_bist_5m() -> pd.DataFrame:
    return _multi_day_bist(5, 96)


def _multi_day_bist_15m() -> pd.DataFrame:
    return _multi_day_bist(15, 32)


def test_pipeline_fetches_caches_and_resamples(tmp_path) -> None:
    provider = _Provider(_base_frame())
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)
    policy = ResamplePolicy(
        target_timeframe="15m",
        rule="15min",
        expected_base_bars=3,
        origin="start_day",
        offset="10h",
    )

    result = pipeline.refresh(
        symbol="THYAO",
        base_timeframe="5m",
        start=datetime.fromisoformat("2026-08-19T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-19T10:30:00+03:00"),
        policies=(policy,),
    )

    assert provider.calls == 1
    assert len(result.base) == 6
    assert len(result.derived["15m"]) == 2
    first = result.derived["15m"].iloc[0]
    assert first["open"] == 100
    assert first["high"] == 103
    assert first["low"] == 99
    assert first["close"] == 102.5
    assert first["volume"] == 60
    assert bool(first["is_complete"])
    assert store.latest_timestamp("THYAO", "5m") == pd.Timestamp("2026-08-19T10:25:00+03:00")
    assert store.latest_timestamp("THYAO", "15m") == pd.Timestamp("2026-08-19T10:15:00+03:00")


def test_incremental_refresh_does_not_backfill_but_full_refresh_extends_left_edge(
    tmp_path,
) -> None:
    full_frame = _base_frame()
    recent_frame = full_frame.iloc[-2:].reset_index(drop=True)
    store = ParquetOHLCVStore(tmp_path)
    store.merge_and_save(
        recent_frame,
        symbol="THYAO",
        timeframe="5m",
        source="fixture",
    )
    pipeline = MarketDataPipeline(_Provider(full_frame), store)
    requested_start = datetime.fromisoformat("2026-08-19T10:00:00+03:00")
    end = datetime.fromisoformat("2026-08-19T10:30:00+03:00")

    incremental_start = pipeline.incremental_bist_start(
        symbol="THYAO",
        requested_start=requested_start,
    )
    assert pd.Timestamp(incremental_start) == pd.Timestamp(
        "2026-08-19T10:20:00+03:00"
    )
    assert pd.Timestamp(incremental_start) > pd.Timestamp(requested_start)

    backfilled = pipeline.refresh_bist_5m(
        symbol="THYAO",
        start=requested_start,
        end=end,
        target_timeframes=("1h",),
    )
    assert len(backfilled.base) == len(full_frame)
    assert backfilled.base.iloc[0]["timestamp"] == pd.Timestamp(
        "2026-08-19T10:00:00+03:00"
    )


def test_bist_pipeline_builds_all_default_timeframes(tmp_path) -> None:
    provider = _Provider(_multi_day_bist_5m())
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)

    result = pipeline.refresh_bist_5m(
        symbol="THYAO",
        start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-04T18:00:00+03:00"),
    )

    assert provider.calls == 1
    assert set(result.derived) == {"15m", "30m", "1h", "2h", "4h", "1d"}
    assert len(result.base) == 12 * 96
    assert len(result.derived["1h"]) == 12 * 8
    assert len(result.derived["4h"]) == 12 * 2
    assert len(result.derived["1d"]) == 12
    for frame in result.derived.values():
        assert frame["is_complete"].all()
        assert frame["is_closed"].all()


def test_bist_15m_pipeline_builds_analysis_timeframes(tmp_path) -> None:
    provider = _Provider(_multi_day_bist_15m())
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)

    result = pipeline.refresh_bist_15m(
        symbol="THYAO",
        start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-04T18:00:00+03:00"),
    )

    assert provider.calls == 1
    assert provider.timeframes == ["15m"]
    assert set(result.derived) == {"30m", "1h", "2h", "4h", "1d"}
    assert len(result.base) == 12 * 32
    assert len(result.derived["30m"]) == 12 * 16
    assert len(result.derived["1h"]) == 12 * 8
    assert len(result.derived["2h"]) == 12 * 4
    assert len(result.derived["4h"]) == 12 * 2
    assert len(result.derived["1d"]) == 12
    for frame in result.derived.values():
        assert frame["is_complete"].all()
        assert frame["is_closed"].all()


def test_bist_15m_incremental_overlap_uses_15_minutes(tmp_path) -> None:
    frame = _multi_day_bist_15m().iloc[:8].reset_index(drop=True)
    store = ParquetOHLCVStore(tmp_path)
    store.merge_and_save(frame, symbol="THYAO", timeframe="15m", source="fixture")
    pipeline = MarketDataPipeline(_Provider(frame), store)

    start = pipeline.incremental_bist_start(
        symbol="THYAO",
        requested_start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        base_timeframe="15m",
        overlap_bars=1,
    )

    latest = store.latest_timestamp("THYAO", "15m")
    assert latest is not None
    assert pd.Timestamp(start) == latest - pd.Timedelta(minutes=15)


def test_bist_pipeline_one_hour_output_replays_in_market_structure(tmp_path) -> None:
    provider = _Provider(_multi_day_bist_15m())
    store = ParquetOHLCVStore(tmp_path)
    pipeline = MarketDataPipeline(provider, store)

    result = pipeline.refresh_bist_15m(
        symbol="THYAO",
        start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-04T18:00:00+03:00"),
        target_timeframes=("1h",),
    )
    one_hour = result.derived["1h"]

    config = MarketStructureConfig(
        external_pivot_len=3,
        internal_pivot_len=2,
        atr_length=5,
        external_min_atr_distance=0.10,
        internal_min_atr_distance=0.10,
        min_tick=0.01,
    )
    first_engine = MarketStructureEngine(config)
    second_engine = MarketStructureEngine(config)

    first = first_engine.replay(one_hour)
    second = second_engine.replay(one_hour)

    assert first
    assert len(first) == len(one_hour)
    assert [(r.timestamp, r.state, r.direction, r.score, r.events) for r in first] == [
        (r.timestamp, r.state, r.direction, r.score, r.events) for r in second
    ]
    assert first_engine.export_contract is not None
    assert first_engine.export_contract.handshake == 314159.0
